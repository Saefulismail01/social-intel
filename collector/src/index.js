import { chromium } from "playwright-core";
import { sanitizeFeed } from "./normalize.js";

const CDP_URL = process.env.SI_CDP_URL ?? "http://127.0.0.1:9222";
const API_URL = process.env.SI_API_URL ?? "http://127.0.0.1:8000";
const ENDPOINTS = new Set([
  "/bapi/composite/v9/friendly/pgc/feed/feed-recommend/list",
  "/bapi/composite/v2/friendly/pgc/feed/search/list",
]);
const attachedPages = new WeakSet();
let batchesObserved = 0;
let postsMatched = 0;
let postsUnmatched = 0;
const MAX_QUEUE = 20;
let queue = Promise.resolve();
let queued = 0;

async function trackedSymbols() {
  const response = await fetch(`${API_URL}/api/universe`);
  if (!response.ok) throw new Error(`universe_http_${response.status}`);
  return (await response.json()).map(row => row.symbol);
}

async function forward(posts) {
  if (!posts.length) return;
  const response = await fetch(`${API_URL}/api/ingest`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ source: "binance-square-browser", collected_at: new Date().toISOString(), posts }),
  });
  if (!response.ok) throw new Error(`ingest_http_${response.status}`);
  const result = await response.json();
  console.log(JSON.stringify({ event: "square_ingested", inserted: result.inserted, updated: result.updated, affected: result.affected_symbols }));
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
    if (response.status() !== 200 || !ENDPOINTS.has(url.pathname)) return;
    enqueue(async () => {
      const [payload, symbols] = await Promise.all([response.json(), trackedSymbols()]);
      const posts = sanitizeFeed(payload, symbols);
      batchesObserved += 1;
      postsMatched += posts.length;
      postsUnmatched += Math.max(0, Array.isArray(payload?.data?.vos) ? payload.data.vos.length - posts.length : 0);
      await fetch(`${API_URL}/api/collector/heartbeat`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ source: "binance-square-browser", status: "FEED_OBSERVED", batches: batchesObserved, matched: postsMatched, unmatched: postsUnmatched }),
      });
      console.log(JSON.stringify({ event: "feed_batch_observed", batch: batchesObserved, matched: posts.length, matched_total: postsMatched, unmatched_total: postsUnmatched }));
      await forward(posts);
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
