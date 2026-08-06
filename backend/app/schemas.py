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


class SanitizedSquarePost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_post_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    author_id: str = Field(min_length=1, max_length=128)
    author_name: str = Field(default="", max_length=256)
    text: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    public_url: Optional[str] = Field(default=None, max_length=2_000)
    symbols: list[str] = Field(default_factory=list, max_length=50)
    verification_type: Optional[int] = None
    engagement: PublicEngagement = Field(default_factory=PublicEngagement)
    card_type: str = Field(default="", max_length=64)
    content_type: Optional[int] = None

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

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if value > now + timedelta(minutes=10):
            raise ValueError("observed_at is too far in the future")
        if value < now - timedelta(days=3650):
            raise ValueError("observed_at is outside retention limits")
        return value


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
