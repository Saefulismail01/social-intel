import test from "node:test";
import assert from "node:assert/strict";
import {
  squareFeedBody,
  detectionPathFor,
  postTimestamp,
  trackBounds,
  deriveSearchStatus,
  searchCoverageBody,
} from "../src/feed.js";

test("squareFeedBody wraps the raw feed verbatim with source + collected_at + detection_path", () => {
  const feed = { data: { vos: [{ id: "1", squareAuthorId: "a" }] } };
  const body = squareFeedBody(feed, "feed-recommend", new Date("2026-08-06T12:00:00Z"));
  assert.equal(body.source, "binance-square-browser");
  assert.equal(body.collected_at, "2026-08-06T12:00:00.000Z");
  assert.equal(body.detection_path, "feed-recommend");
  // The raw payload is forwarded verbatim — no client-side normalization.
  assert.equal(body.feed, feed);
  assert.deepEqual(body.feed.data.vos[0], { id: "1", squareAuthorId: "a" });
});

test("squareFeedBody defaults detection_path to feed-recommend", () => {
  const body = squareFeedBody({ data: { vos: [] } });
  assert.equal(body.detection_path, "feed-recommend");
  assert.deepEqual(Object.keys(body).sort(), ["collected_at", "detection_path", "feed", "source"]);
});

test("detectionPathFor maps /bapi endpoints to their path names", () => {
  assert.equal(detectionPathFor("/bapi/composite/v9/friendly/pgc/feed/feed-recommend/list"), "feed-recommend");
  assert.equal(detectionPathFor("/bapi/composite/v2/friendly/pgc/feed/search/list"), "feed-search");
  assert.equal(detectionPathFor("/some/other/endpoint"), null);
});

test("postTimestamp treats seconds and milliseconds the same as the server normalizer", () => {
  const seconds = Math.floor(Date.now() / 1000);
  assert.equal(postTimestamp({ date: seconds }), postTimestamp({ date: seconds * 1000 }));
  assert.equal(postTimestamp({ date: seconds * 1000 }), seconds * 1000);
  assert.equal(postTimestamp({ date: "nan" }), null);
  assert.equal(postTimestamp({}), null);
});

test("trackBounds tracks oldest and newest across a stream of timestamps", () => {
  let [oldest, newest] = [null, null];
  [oldest, newest] = trackBounds(oldest, newest, 300);
  assert.equal(oldest, 300);
  assert.equal(newest, 300);
  [oldest, newest] = trackBounds(oldest, newest, 100);
  assert.equal(oldest, 100);
  assert.equal(newest, 300);
  [oldest, newest] = trackBounds(oldest, newest, 500);
  assert.equal(oldest, 100);
  assert.equal(newest, 500);
  // null / non-finite timestamps must not perturb the bounds.
  [oldest, newest] = trackBounds(oldest, newest, null);
  assert.equal(oldest, 100);
  assert.equal(newest, 500);
});

test("deriveSearchStatus tells scanned-but-empty from no-results from partial", () => {
  assert.equal(deriveSearchStatus({ responses: 0, matchedPosts: 0, oldest: null, cutoff: 0, unchanged: 0 }), "EMPTY");
  assert.equal(deriveSearchStatus({ responses: 3, matchedPosts: 0, oldest: null, cutoff: 0, unchanged: 0 }), "NO_RESULTS");
  assert.equal(deriveSearchStatus({ responses: 3, matchedPosts: 5, oldest: 100, cutoff: 0, unchanged: 0 }), "SUCCESS");
  // Oldest is still newer than the cutoff and pagination stalled -> partial.
  assert.equal(deriveSearchStatus({ responses: 3, matchedPosts: 5, oldest: 200, cutoff: 100, unchanged: 2 }), "PARTIAL_HISTORY");
  // Reached the cutoff -> success, even if pagination stalled afterwards.
  assert.equal(deriveSearchStatus({ responses: 3, matchedPosts: 5, oldest: 50, cutoff: 100, unchanged: 2 }), "SUCCESS");
});

test("searchCoverageBody serializes oldest/newest/cutoff as ISO and nulls absent ones", () => {
  const body = searchCoverageBody({
    symbol: "ZFEED", status: "SUCCESS", pagesScanned: 3, responses: 3, matchedPosts: 7,
    startedAt: "2026-08-06T12:00:00.000Z", oldest: 1722936000000, newest: 1722940000000,
    cutoff: 1722849600000, message: "",
  });
  assert.equal(body.symbol, "ZFEED");
  assert.equal(body.query, "ZFEED");
  assert.equal(body.status, "SUCCESS");
  assert.equal(body.pages_scanned, 3);
  assert.equal(body.matched_posts, 7);
  assert.equal(body.oldest_post_at, new Date(1722936000000).toISOString());
  assert.equal(body.newest_post_at, new Date(1722940000000).toISOString());
  assert.equal(body.cutoff_at, new Date(1722849600000).toISOString());
  assert.equal(body.message, null);
});

test("searchCoverageBody nulls timestamps when no posts were found", () => {
  const body = searchCoverageBody({
    symbol: "ZFEED", status: "NO_RESULTS", pagesScanned: 2, responses: 2, matchedPosts: 0,
    startedAt: "2026-08-06T12:00:00.000Z", oldest: null, newest: null, cutoff: 1722849600000,
    message: "nothing here",
  });
  assert.equal(body.status, "NO_RESULTS");
  assert.equal(body.oldest_post_at, null);
  assert.equal(body.newest_post_at, null);
  assert.equal(body.cutoff_at, new Date(1722849600000).toISOString());
  assert.equal(body.message, "nothing here");
});

test("the passive collector forwards the raw payload without client-side normalization", () => {
  // This is a contract assertion: squareFeedBody must carry the feed untouched,
  // so the desk — not the collector — is the authority on tracked symbols.
  const raw = { data: { vos: [{ id: "1", squareAuthorId: "a", title: "$ZFEED" }] }, extra: { headers: {} } };
  const body = squareFeedBody(raw, "feed-search");
  assert.equal(body.feed, raw);
  assert.equal(body.detection_path, "feed-search");
});
