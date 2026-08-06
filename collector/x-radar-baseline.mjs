#!/usr/bin/env node
/**
 * Read X Radar's daily post counts and store them as the harvest baseline.
 *
 * Radar (x.com/i/radar) is a volume oracle, not a data source. On this
 * subscription its config reports:
 *
 *   allow_post_counts: true      allow_unique_users: false
 *   allow_impressions: false     allow_granularity: ["Day"]
 *   time_range_limit_days: 7     saved_query_limit: 5
 *
 * So it yields counts only — no posts, authors, or engagement — which is
 * exactly the denominator the slice harvester lacks. Counts land in
 * /api/x-baseline; the radar then reports "captured N of M" instead of a bare N.
 *
 * Only `$SYMBOL` queries are mapped. A bare-word query like "Aster" counts every
 * mention of the word, which is a different question from the cashtag and must
 * not be attributed to the token.
 */
import { chromium } from "playwright-core";

const CDP_URL = process.env.SI_CDP_URL ?? "http://127.0.0.1:9222";
const API_URL = (process.env.SI_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const SETTLE_MS = Number(process.env.SI_RADAR_SETTLE_MS ?? 15000);

const CASHTAG_ONLY = /^\$([A-Za-z][A-Za-z0-9]{0,31})$/;

function symbolFor(advancedQuery) {
  const match = CASHTAG_ONLY.exec(String(advancedQuery ?? "").trim());
  return match ? match[1].toUpperCase() : null;
}

/** Pull every insight rule out of a Radar GraphQL payload, whatever the shape. */
function extractRules(payload) {
  const result = payload?.data?.viewer_v2?.user_results?.result;
  if (!result) return [];
  const single = result.insight_rule_by_id;
  const list = result.insight_rules?.items;
  return [single, ...(Array.isArray(list) ? list : [])].filter(Boolean);
}

function toReport(rule) {
  const query = rule?.core?.advanced_query;
  const symbol = symbolFor(query);
  if (!symbol) return null;
  // matched_post_counts is the authoritative series; `preview` is a truncated
  // teaser shown in the list and must not be preferred over it.
  const counts = rule?.matched_post_counts?.counts ?? rule?.preview?.counts;
  if (!Array.isArray(counts) || !counts.length) return null;
  return {
    symbol,
    query: String(query),
    source: "x-radar",
    days: counts
      .filter(entry => Number.isFinite(entry?.start_time))
      .map(entry => ({
        day: new Date(entry.start_time * 1000).toISOString(),
        post_count: Math.max(0, Number(entry.count) || 0),
      })),
  };
}

const browser = await chromium.connectOverCDP(CDP_URL);
const context = browser.contexts()[0];
const page = await context.newPage();

const byQuery = new Map();
page.on("response", async response => {
  if (!/usePostCountQuery|insightsListContextQuery/.test(response.url())) return;
  let payload;
  try {
    payload = await response.json();
  } catch {
    return;
  }
  for (const rule of extractRules(payload)) {
    const report = toReport(rule);
    // A later usePostCountQuery supersedes the list preview for the same query.
    if (report && (!byQuery.has(report.query) || rule.matched_post_counts)) {
      byQuery.set(report.query, report);
    }
  }
});

let exitCode = 0;
try {
  await page.goto("https://x.com/i/radar", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForTimeout(SETTLE_MS);

  if (page.url().includes("/i/flow/login")) {
    throw new Error("X session expired — re-run collector/x-session-import.mjs");
  }
  const reports = [...byQuery.values()];
  if (!reports.length) {
    throw new Error("no cashtag ($SYMBOL) Radar queries found; add one in the Radar UI");
  }

  const response = await fetch(`${API_URL}/api/x-baseline`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(reports),
  });
  if (!response.ok) throw new Error(`x-baseline http ${response.status}`);
  const result = await response.json();

  console.log(JSON.stringify({
    event: "x_radar_baseline_complete",
    queries: reports.map(item => ({
      symbol: item.symbol,
      days: item.days.length,
      total: item.days.reduce((sum, day) => sum + day.post_count, 0),
    })),
    ...result,
  }));
} catch (error) {
  console.error(JSON.stringify({ event: "x_radar_baseline_error", message: String(error.message).slice(0, 300) }));
  exitCode = 1;
} finally {
  await page.close();
  await browser.close();
}
process.exit(exitCode);
