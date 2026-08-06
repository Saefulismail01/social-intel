import { chromium } from "playwright-core";
import { squareFeedBody, postTimestamp, trackBounds, deriveSearchStatus, searchCoverageBody } from "./feed.js";

const CDP_URL = process.env.SI_CDP_URL ?? "http://127.0.0.1:9222";
const API_URL = process.env.SI_API_URL ?? "http://127.0.0.1:8000";
const MAX_PAGES = Number(process.env.SI_SQUARE_SEARCH_PAGES ?? 5);
const CUTOFF_DAYS = Number(process.env.SI_SQUARE_SEARCH_DAYS ?? 7);
const WAIT_MS = Number(process.env.SI_SQUARE_SEARCH_WAIT_MS ?? 5000);
// Pause between symbols so search traffic does not trip WAF; keep short on VPS.
const SYMBOL_GAP_MS = Number(process.env.SI_SQUARE_SYMBOL_GAP_MS ?? 30_000);

async function post(path, body) {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path}_http_${response.status}`);
  return response.json();
}

async function universe() {
  const response = await fetch(`${API_URL}/api/universe`);
  if (!response.ok) throw new Error(`universe_http_${response.status}`);
  return response.json();
}

function searchUrl(symbol) {
  return `https://www.binance.com/en/square/search?s=${encodeURIComponent(symbol)}`;
}

async function scan(page, symbol) {
  const startedAt = new Date().toISOString();
  const cutoff = Date.now() - CUTOFF_DAYS * 86_400_000;
  let responses = 0;
  let pagesScanned = 0;
  let matchedPosts = 0;
  let oldest = null;
  let newest = null;
  let unchanged = 0;
  const postIds = new Set();
  // Buffer each matching search-list response so its posts are actually
  // ingested, not merely counted. The passive collector misses these because
  // search pages are only loaded during a targeted scan.
  const feeds = [];
  const handler = async response => {
    let url;
    try { url = new URL(response.url()); } catch { return; }
    if (url.pathname !== "/bapi/composite/v2/friendly/pgc/feed/search/list" || response.status() !== 200) return;
    try {
      const requestBody = response.request().postDataJSON();
      if (requestBody?.type !== 1 || String(requestBody?.searchContent).toUpperCase() !== symbol) return;
      const payload = await response.json();
      const items = Array.isArray(payload?.data?.vos) ? payload.data.vos.filter(item => item?.id && item?.squareAuthorId) : [];
      responses += 1;
      pagesScanned = Math.max(pagesScanned, Number(requestBody?.pageIndex) || 1);
      feeds.push(payload);
      for (const item of items) {
        postIds.add(String(item.id));
        const timestamp = postTimestamp(item);
        if (timestamp != null) [oldest, newest] = trackBounds(oldest, newest, timestamp);
      }
      matchedPosts = postIds.size;
    } catch {}
  };
  page.on("response", handler);
  let status = "SUCCESS";
  let message = "";
  try {
    await page.goto(searchUrl(symbol), { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForTimeout(WAIT_MS);
    const body = (await page.locator("body").innerText()).toLowerCase();
    if (body.includes("captcha") || body.includes("security verification")) throw new Error("CHALLENGE");
    if (body.includes("log in") && !body.includes("search results")) throw new Error("LOGIN_REQUIRED");
    for (let index = 1; index < MAX_PAGES && (!oldest || oldest > cutoff); index += 1) {
      const before = postIds.size;
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await page.waitForTimeout(WAIT_MS);
      unchanged = postIds.size === before ? unchanged + 1 : 0;
      if (unchanged >= 2) break;
    }
    // Status is derived from what the search actually returned, so the desk can
    // tell "scanned and found nothing" from "scanned but did not reach the
    // cutoff" from "scanned and complete".
    status = deriveSearchStatus({ responses, matchedPosts, oldest, cutoff, unchanged });
  } catch (error) {
    status = ["CHALLENGE", "LOGIN_REQUIRED"].includes(error.message) ? error.message : "ERROR";
    message = error.message;
  } finally {
    page.off("response", handler);
  }

  // Ingest every matching search-list response via the server-side normalizer.
  // A search that discovered posts but never stored them was a coverage row
  // pointing at evidence the desk never held. The search path is tagged
  // explicitly so provenance is preserved (teardown §16).
  let ingested = 0;
  for (const feed of feeds) {
    try {
      const result = await post("/api/square/feed", squareFeedBody(feed, "feed-search"));
      ingested += result.normalized ?? 0;
    } catch (error) {
      console.error(JSON.stringify({ event: "square_feed_failed", symbol, category: error.message }));
    }
  }

  await post("/api/search-coverage", searchCoverageBody({
    symbol, status, pagesScanned, responses, matchedPosts, startedAt,
    oldest, newest, cutoff, message,
  }));
  console.log(JSON.stringify({ event: "square_search_complete", symbol, status, pages_scanned: pagesScanned, matched_posts: matchedPosts, ingested }));
  return status;
}

async function main() {
  const requested = process.argv.slice(2).map(value => value.toUpperCase().replace(/USDT$/, ""));
  const rows = await universe();
  const allowed = new Set(rows.map(row => row.symbol));
  const symbols = requested.length ? requested.filter(symbol => allowed.has(symbol)) : rows.filter(row => row.priority <= 1).map(row => row.symbol);
  if (!symbols.length) throw new Error("no_tracked_symbols_selected");
  const browser = await chromium.connectOverCDP(CDP_URL);
  const context = browser.contexts()[0];
  if (!context) throw new Error("browser_context_missing");
  const page = context.pages()[0] ?? await context.newPage();
  for (const symbol of symbols) {
    const status = await scan(page, symbol);
    if (["CHALLENGE", "LOGIN_REQUIRED"].includes(status)) break;
    await page.waitForTimeout(SYMBOL_GAP_MS);
  }
  await browser.close();
}

main().catch(error => { console.error(JSON.stringify({ event: "square_search_fatal", category: error.message })); process.exitCode = 1; });
