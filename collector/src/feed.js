// Pure helpers for the Square collector's feed + search paths. Kept free of
// fetch/playwright so they can be unit-tested directly.

const CUTOFF_DAYS = Number(process.env.SI_SQUARE_SEARCH_DAYS ?? 7);

// Detection path per observed /bapi surface (teardown §5/§16).
export function detectionPathFor(pathname) {
  if (pathname === "/bapi/composite/v9/friendly/pgc/feed/feed-recommend/list") return "feed-recommend";
  if (pathname === "/bapi/composite/v2/friendly/pgc/feed/search/list") return "feed-search";
  return null;
}

export function squareFeedBody(payload, detectionPath = "feed-recommend", now = new Date()) {
  return {
    source: "binance-square-browser",
    collected_at: now.toISOString(),
    feed: payload,
    detection_path: detectionPath,
  };
}

export function cutoffMs(days = CUTOFF_DAYS) {
  return Date.now() - days * 86_400_000;
}

// Derive a post timestamp (ms) from a Square item's `date` field, mirroring the
// server-side normalizer's seconds-vs-millis handling.
export function postTimestamp(item) {
  let timestamp = Number(item?.date);
  if (!Number.isFinite(timestamp)) return null;
  if (timestamp && timestamp < 10_000_000_000) timestamp *= 1000;
  return timestamp;
}

// Track oldest/newest across a stream of items; null-safe.
export function trackBounds(oldest, newest, timestamp) {
  if (timestamp == null || !Number.isFinite(timestamp)) return [oldest, newest];
  if (oldest == null && newest == null) return [timestamp, timestamp];
  return [Math.min(oldest, timestamp), Math.max(newest, timestamp)];
}

// Derive the search-coverage status from what the scan actually observed. The
// desk must be able to tell "scanned, found nothing" from "scanned but did not
// reach the cutoff" from "scanned and complete".
export function deriveSearchStatus({ responses, matchedPosts, oldest, cutoff, unchanged }) {
  if (!responses) return "EMPTY";
  if (!matchedPosts) return "NO_RESULTS";
  if (oldest != null && cutoff != null && oldest > cutoff && unchanged >= 2) return "PARTIAL_HISTORY";
  return "SUCCESS";
}

export function searchCoverageBody({
  symbol, status, pagesScanned, responses, matchedPosts, startedAt,
  oldest, newest, cutoff, message,
}) {
  return {
    symbol, query: symbol, status,
    pages_scanned: pagesScanned, responses_observed: responses, matched_posts: matchedPosts,
    started_at: startedAt,
    oldest_post_at: oldest != null ? new Date(oldest).toISOString() : null,
    newest_post_at: newest != null ? new Date(newest).toISOString() : null,
    cutoff_at: cutoff != null ? new Date(cutoff).toISOString() : null,
    message: message || null,
  };
}
