import { chromium } from "playwright-core";
import { squareFeedBody, detectionPathFor } from "./feed.js";

const CDP_URL = process.env.SI_CDP_URL ?? "http://127.0.0.1:9222";
const API_URL = process.env.SI_API_URL ?? "http://127.0.0.1:8000";
const attachedPages = new WeakSet();
let batchesObserved = 0;
let postsMatched = 0;
let postsUnmatched = 0;
const MAX_QUEUE = 20;
let queue = Promise.resolve();
let queued = 0;

async function post(path, body) {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path}_http_${response.status}`);
  return response.json();
}

async function forwardRaw(payload, detectionPath) {
  // Send the verbatim feed response to the desk; the server is the only place
  // that knows which symbols are tracked, so normalizing here would let the
  // collector's universe drift out of sync with ingestion. The detection path
  // is passed through so each derived post carries its provenance.
  const response = await fetch(`${API_URL}/api/square/feed`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(squareFeedBody(payload, detectionPath)),
  });
  if (!response.ok) throw new Error(`square_feed_http_${response.status}`);
  const result = await response.json();
  batchesObserved += 1;
  // `normalized` is how many posts survived server-side tracking; everything
  // else in the batch was an untracked symbol, not a failure.
  const matched = result.normalized ?? 0;
  const observed = Array.isArray(payload?.data?.vos) ? payload.data.vos.length : 0;
  postsMatched += matched;
  postsUnmatched += Math.max(0, observed - matched);
  console.log(JSON.stringify({ event: "square_feed_forwarded", batch: batchesObserved, detection_path: detectionPath, normalized: matched, inserted: result.inserted, updated: result.updated, affected: result.affected_symbols }));
  return { matched, observed };
}

async function reportFeedCoverage(detectionPath, batchAt, cardsObserved, matchedPosts, symbolsCovered) {
  try {
    await fetch(`${API_URL}/api/square-feed-coverage`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify([{ detection_path: detectionPath, batch_at: batchAt, cards_observed: cardsObserved, matched_posts: matchedPosts, symbols_covered: symbolsCovered }]),
    });
  } catch (error) {
    console.error(JSON.stringify({ event: "feed_coverage_report_failed", category: error.message }));
  }
}

function enqueue(task) {
  if (queued >= MAX_QUEUE) { console.error(JSON.stringify({ event: "queue_full" })); return; }
  queued += 1;
  queue = queue.then(task).catch(error => console.error(JSON.stringify({ event: "collector_error", category: error.message }))).finally(() => { queued -= 1; });
}

function attach(page) {
  if (attachedPages.has(page)) return;
  attachedPages.add(page);
  page.on("response", response => {
    let url;
    try { url = new URL(response.url()); } catch { return; }
    if (response.status() !== 200) return;
    const detectionPath = detectionPathFor(url.pathname);
    if (!detectionPath) return;
    enqueue(async () => {
      const batchAt = new Date().toISOString();
      const payload = await response.json();
      const { matched, observed } = await forwardRaw(payload, detectionPath);
      const rawSymbols = (payload?.data?.vos ?? []).flatMap(item => [
        ...(item?.tradingPairsV2 ?? []).map(p => p?.symbol ?? "").filter(Boolean),
        ...(item?.userInputTradingPairs ?? []).map(p => p?.symbol ?? "").filter(Boolean),
      ]);
      const symbolsCovered = [...new Set(rawSymbols.map(s => s.toUpperCase().replace(/USDT$/, "")))];
      await reportFeedCoverage(detectionPath, batchAt, observed, matched, symbolsCovered);
      await post("/api/collector/heartbeat", {
        source: "binance-square-browser", status: "FEED_OBSERVED",
        batches: batchesObserved, matched: postsMatched, unmatched: postsUnmatched,
      });
      console.log(JSON.stringify({ event: "feed_batch_observed", batch: batchesObserved, detection_path: detectionPath, matched, matched_total: postsMatched, unmatched_total: postsUnmatched }));
    });
  });
}

async function main() {
  const browser = await chromium.connectOverCDP(CDP_URL);
  const context = browser.contexts()[0];
  if (!context) throw new Error("browser_context_missing");
  context.pages().forEach(attach);
  context.on("page", attach);
  console.log(JSON.stringify({ event: "collector_ready", mode: "passive", pages: context.pages().length }));
  await new Promise(resolve => browser.on("disconnected", resolve));
  console.error(JSON.stringify({ event: "browser_disconnected" }));
}

main().catch(error => { console.error(JSON.stringify({ event: "collector_fatal", category: error.message })); process.exitCode = 1; });
