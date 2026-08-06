from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .schemas import PublicEngagement, SanitizedSquarePost


TICKER_RE = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9]{1,19})\b", re.IGNORECASE)


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


def _counter(item: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        value = item.get(name)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


def normalize_feed_response(payload: dict[str, Any], tracked_symbols: set[str]) -> list[SanitizedSquarePost]:
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

        posts.append(SanitizedSquarePost(
            source_post_id=str(item["id"]),
            observed_at=_timestamp(item.get("date")),
            author_id=str(item["squareAuthorId"]),
            author_name=str(item.get("authorName") or ""),
            text=text,
            public_url=item.get("webLink") or item.get("shareLink"),
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
        ))
    return posts
