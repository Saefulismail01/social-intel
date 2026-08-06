from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FORBIDDEN_KEY_PARTS = {
    "authorization", "cookie", "csrf", "token", "secret", "password",
    "fingerprint", "device-info", "device_info", "fvideo", "session",
    "aws-waf", "credential", "x-trace", "bnc-uuid",
}
MAX_POSTS_PER_BATCH = 100
MAX_TEXT_LENGTH = 20_000


def reject_sensitive_keys(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise ValueError(f"Sensitive field rejected at {path}.{key}")
            reject_sensitive_keys(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_sensitive_keys(nested, f"{path}[{index}]")


class PublicEngagement(BaseModel):
    likes: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)
    views: int = Field(default=0, ge=0)


# Detection paths map to the Square /bapi feed family observed at the desk
# (teardown §5/§15.2). A single post may surface through more than one path;
# the ingest layer merges observations and keeps the first_detected_at.
SQUARE_DETECTION_PATHS = {
    "feed-recommend",  # /bapi/composite/v9/friendly/pgc/feed/feed-recommend/list
    "feed-search",     # /bapi/composite/v2/friendly/pgc/feed/search/list
    "fixture",         # local fixture / backfill (no live surface)
}


class SanitizedSquarePost(BaseModel):
    """A normalized Binance Square PGC card (teardown §15.3 field map).

    `published_at` is the timestamp Binance recorded on the card (`date`).
    `detected_at` is when the desk first saw the card (wall clock of the
    collector batch). Distinct timestamps preserve the detection-latency
    signal that 1322 productizes (§15.6) without claiming their internal
    measurement — we only know our own observation clock.
    """

    model_config = ConfigDict(extra="forbid")

    source_post_id: str = Field(min_length=1, max_length=128)
    published_at: datetime
    detected_at: Optional[datetime] = None
    author_id: str = Field(min_length=1, max_length=128)
    author_name: str = Field(default="", max_length=256)
    text: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    public_url: Optional[str] = Field(default=None, max_length=2_000)
    share_url: Optional[str] = Field(default=None, max_length=2_000)
    symbols: list[str] = Field(default_factory=list, max_length=50)
    verification_type: Optional[int] = None
    engagement: PublicEngagement = Field(default_factory=PublicEngagement)
    card_type: str = Field(default="", max_length=64)
    content_type: Optional[int] = None
    # Native PGC fields mirrored from the Square card (§15.3/§15.4/§15.5).
    coin_pairs: list[str] = Field(default_factory=list, max_length=50)
    tendency: Optional[str] = Field(default=None, pattern=r"^(bullish|bearish)$")
    bullish_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bearish_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    hashtags: list[str] = Field(default_factory=list, max_length=100)
    mentions: list[str] = Field(default_factory=list, max_length=100)
    is_reply: bool = False
    parent_id: Optional[str] = Field(default=None, max_length=128)
    is_sticky: bool = False
    media_urls: list[str] = Field(default_factory=list, max_length=50)
    # Provenance of this particular observation (teardown §16).
    detection_path: str = Field(default="fixture", pattern=r"^[a-z0-9-]{1,32}$")

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            symbol = value.upper().strip()
            if symbol.endswith("USDT"):
                symbol = symbol[:-4]
            if symbol and symbol.isalnum() and symbol not in normalized:
                normalized.append(symbol)
        return normalized

    @field_validator("coin_pairs")
    @classmethod
    def normalize_coin_pairs(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            pair = value.upper().strip().replace("/", "").replace("_", "")
            if pair and pair.isalnum() and pair not in normalized:
                normalized.append(pair)
        return normalized

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if value > now + timedelta(minutes=10):
            raise ValueError("published_at is too far in the future")
        if value < now - timedelta(days=3650):
            raise ValueError("published_at is outside retention limits")
        return value

    @field_validator("detected_at")
    @classmethod
    def validate_detected_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def default_detected_from_published(self) -> "SanitizedSquarePost":
        if self.detected_at is None:
            self.detected_at = self.published_at
        return self


class SanitizedIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="binance-square-browser", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    collected_at: datetime
    posts: list[SanitizedSquarePost] = Field(max_length=MAX_POSTS_PER_BATCH)

    @model_validator(mode="before")
    @classmethod
    def reject_secrets(cls, value: Any) -> Any:
        reject_sensitive_keys(value)
        return value


# A targeted Square search that found nothing useful is a real outcome, not a
# failure; these statuses make that distinction explicit instead of collapsing
# every result into "SUCCESS".
SEARCH_COVERAGE_STATUSES = frozenset({
    "SUCCESS",        # posts discovered and the cutoff was reached
    "PARTIAL_HISTORY",  # posts discovered but the oldest predates the cutoff
    "NO_RESULTS",     # the search ran but matched no posts
    "EMPTY",          # the search backend returned no responses at all
    "SATURATED",      # a page was capped; more history likely exists unseen
    "CHALLENGE",      # a captcha / verification wall blocked the scan
    "LOGIN_REQUIRED",  # Binance demanded a login the session did not have
    "ERROR",          # an unexpected error interrupted the scan
})


class SearchCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    query: str = Field(default="", max_length=128)
    status: str = Field(default="SUCCESS", max_length=32)
    pages_scanned: int = Field(default=0, ge=0)
    responses_observed: int = Field(default=0, ge=0)
    matched_posts: int = Field(default=0, ge=0)
    started_at: datetime
    oldest_post_at: Optional[datetime] = None
    newest_post_at: Optional[datetime] = None
    cutoff_at: Optional[datetime] = None
    message: Optional[str] = Field(default=None, max_length=256)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.upper().strip()
        if symbol.endswith("USDT"):
            symbol = symbol[:-4]
        if not symbol or not symbol.isalnum():
            raise ValueError("symbol must be alphanumeric")
        return symbol

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in SEARCH_COVERAGE_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(SEARCH_COVERAGE_STATUSES))}"
            )
        return value

    @field_validator("oldest_post_at", "newest_post_at", "cutoff_at", "started_at")
    @classmethod
    def ensure_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def check_timestamp_order(self) -> "SearchCoverageReport":
        if (
            self.oldest_post_at is not None
            and self.newest_post_at is not None
            and self.oldest_post_at > self.newest_post_at
        ):
            raise ValueError("oldest_post_at cannot be later than newest_post_at")
        return self


class RawSquareFeedRequest(BaseModel):
    """A raw Binance Square feed/search response, normalized server-side.

    The collector forwards the JSON it observed verbatim; the desk is the only
    place that decides which symbols are tracked, so the universe cannot drift
    out of sync between collector and server. Request headers, cookies, and
    browser tokens never enter this object — `reject_secrets` enforces that.

    `detection_path` records which /bapi surface produced this batch
    (teardown §16): 'feed-recommend' for the passive home-timeline observer,
    'feed-search' for the active symbol-search tool. The server passes it
    through to the normalizer so each derived post carries its provenance.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="binance-square-browser", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    collected_at: datetime
    feed: dict[str, Any]
    detection_path: str = Field(default="feed-recommend", pattern=r"^[a-z0-9-]{1,32}$")

    @model_validator(mode="before")
    @classmethod
    def reject_secrets(cls, value: Any) -> Any:
        reject_sensitive_keys(value)
        return value
