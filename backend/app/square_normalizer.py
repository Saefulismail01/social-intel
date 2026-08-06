from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from .schemas import PublicEngagement, SanitizedSquarePost


TICKER_RE = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9]{1,19})\b", re.IGNORECASE)
CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])#([A-Za-z][A-Za-z0-9_]{0,39})\b")
MENTION_RE = re.compile(r"(?<![A-Za-z0-9])@([A-Za-z][A-Za-z0-9_.]{0,39})\b")


def _timestamp(value: Any) -> datetime:
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "subTitle"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return "\n".join(parts)


def _pair_symbols(value: Any) -> list[str]:
    """Extract base-asset symbols from tradingPairsV2 / userInputTradingPairs.

    The native PGC card stores the full pair (e.g. BTCUSDT); the desk's
    symbol universe keys on the base asset (BTC), so we strip the quote.
    """
    symbols: list[str] = []
    if not isinstance(value, list):
        return symbols
    for pair in value:
        if not isinstance(pair, dict):
            continue
        candidates = [
            pair.get("symbol"), pair.get("pair"), pair.get("baseAsset"),
            pair.get("token"), pair.get("name"), pair.get("ticker"),
        ]
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            symbol = candidate.upper().replace("/", "").replace("_", "").strip()
            for quote in ("USDT", "USDC", "FDUSD", "BTC", "BNB"):
                if symbol.endswith(quote) and len(symbol) > len(quote):
                    symbol = symbol[:-len(quote)]
                    break
            if symbol and symbol.isalnum() and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _coin_pairs(value: Any) -> list[str]:
    """Full pair strings (BTCUSDT) as Binance stores them (teardown §15.4).

    Kept distinct from the base-asset `symbols` the desk universe uses, so
    the native pair form is preserved for provenance without polluting the
    symbol axis.
    """
    pairs: list[str] = []
    if not isinstance(value, list):
        return pairs
    for pair in value:
        if not isinstance(pair, dict):
            continue
        for candidate in (pair.get("symbol"), pair.get("pair")):
            if not isinstance(candidate, str):
                continue
            normalized = candidate.upper().replace("/", "").replace("_", "").strip()
            if normalized and normalized.isalnum() and normalized not in pairs:
                pairs.append(normalized)
    return pairs


def _counter(item: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        value = item.get(name)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


def _ratio(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and 0 <= value <= 1:
        return float(value)
    return None


def _tendency(value: Any) -> Optional[str]:
    if isinstance(value, str):
        lowered = value.lower().strip()
        if lowered in ("bullish", "bearish"):
            return lowered
    return None


def _media_urls(item: dict[str, Any]) -> list[str]:
    """CDN URLs from the media list (teardown §15.7 — native bnbstatic)."""
    urls: list[str] = []
    media = item.get("media")
    if not isinstance(media, list):
        return urls
    for entry in media:
        if isinstance(entry, str):
            url = entry.strip()
        elif isinstance(entry, dict):
            url = str(entry.get("url") or entry.get("src") or "").strip()
        else:
            continue
        if url and url not in urls:
            urls.append(url)
    return urls[:50]


def normalize_feed_response(
    payload: dict[str, Any],
    tracked_symbols: set[str],
    detection_path: str = "feed-recommend",
) -> list[SanitizedSquarePost]:
    """Normalize a /bapi PGC feed response into sanitized posts.

    `detection_path` records which feed surface produced this batch
    (teardown §16): 'feed-recommend' for the passive home-timeline observer,
    'feed-search' for the active symbol-search tool. The ingest layer merges
    observations across paths, keeping first_detected_at from the earliest.
    """
    items = payload.get("data", {}).get("vos", [])
    if not isinstance(items, list):
        return []

    posts: list[SanitizedSquarePost] = []
    tracked = {symbol.upper().replace("USDT", "") for symbol in tracked_symbols}
    for item in items:
        if not isinstance(item, dict) or not item.get("id") or not item.get("squareAuthorId"):
            continue
        text = _text(item)
        symbols = _pair_symbols(item.get("tradingPairsV2"))
        symbols += [s for s in _pair_symbols(item.get("userInputTradingPairs")) if s not in symbols]
        for ticker in TICKER_RE.findall(text):
            ticker = ticker.upper()
            if ticker not in symbols:
                symbols.append(ticker)
        symbols = [symbol for symbol in symbols if symbol in tracked]
        if not symbols:
            continue

        coin_pairs = _coin_pairs(item.get("tradingPairsV2"))
        coin_pairs += [p for p in _coin_pairs(item.get("userInputTradingPairs")) if p not in coin_pairs]

        hashtags = [tag for tag in CASHTAG_RE.findall(text)]
        mentions = [m for m in MENTION_RE.findall(text)]

        published_at = _timestamp(item.get("date"))

        posts.append(SanitizedSquarePost(
            source_post_id=str(item["id"]),
            published_at=published_at,
            author_id=str(item["squareAuthorId"]),
            author_name=str(item.get("authorName") or ""),
            text=text,
            public_url=item.get("webLink") or item.get("shareLink"),
            share_url=item.get("shareLink") if isinstance(item.get("shareLink"), str) else None,
            symbols=symbols,
            verification_type=item.get("authorVerificationType") if isinstance(item.get("authorVerificationType"), int) else None,
            engagement=PublicEngagement(
                likes=_counter(item, ("likeCount", "likeCnt", "likes")),
                comments=_counter(item, ("commentCount", "commentCnt", "comments")),
                shares=_counter(item, ("shareCount", "shareCnt", "shares")),
                views=_counter(item, ("viewCount", "viewCnt", "views", "pageView")),
            ),
            card_type=str(item.get("cardType") or ""),
            content_type=item.get("contentType") if isinstance(item.get("contentType"), int) else None,
            coin_pairs=coin_pairs,
            tendency=_tendency(item.get("tendency")),
            bullish_ratio=_ratio(item.get("bullishRatio")),
            bearish_ratio=_ratio(item.get("bearishRatio")),
            hashtags=hashtags,
            mentions=mentions,
            is_reply=bool(item.get("isReply")),
            parent_id=str(item["parentId"]) if item.get("parentId") else None,
            is_sticky=bool(item.get("isSticky")),
            media_urls=_media_urls(item),
            detection_path=detection_path,
        ))
    return posts
