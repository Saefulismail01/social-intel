#!/usr/bin/env node
/**
 * Pull X Radar official daily post counts for tracked cashtags via GraphQL.
 *
 * Premium+ allows 5 saved queries. Counts are durable in /api/x-baseline, so we
 * rotate: create → post-count → delete → next. UI automation is avoided — the
 * Radar page can error while the GraphQL endpoints still work with the live
 * browser session (CDP).
 *
 * Env:
 *   SI_CDP_URL, SI_API_URL, SI_RADAR_SYMBOLS, SI_RADAR_KEEP, SI_RADAR_LIMIT
 *   SI_RADAR_ONLY_MISSING=1  skip symbols that already have *today's* official
 *                            x-radar day (UTC). Historical baselines alone do
 *                            not count — without a re-pull at the day boundary
 *                            the desk shows TODAY=0 for every row.
 *   SI_RADAR_STALE_HOURS=N   with ONLY_MISSING, also re-pull when today's
 *                            baseline was last fetched more than N hours ago
 *                            (keeps the still-filling current day fresh).
 */
import { chromium } from "playwright-core";

const CDP_URL = process.env.SI_CDP_URL ?? "http://127.0.0.1:9222";
const API_URL = (process.env.SI_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const KEEP = new Set(
  (process.env.SI_RADAR_KEEP ?? "TAG")
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean),
);
const LIMIT = process.env.SI_RADAR_LIMIT ? Number(process.env.SI_RADAR_LIMIT) : Infinity;
const ONLY_MISSING = process.env.SI_RADAR_ONLY_MISSING === "1";
const STALE_HOURS = process.env.SI_RADAR_STALE_HOURS
  ? Number(process.env.SI_RADAR_STALE_HOURS)
  : 0;

// queryIds from ondemand.Insights bundle (Aug 2026)
const Q = {
  list: "WWRjBVCm43JPt3KJcYps_A",
  create: "ktnc_IoCymTxsQ4tnPKdHQ",
  delete: "XEhvVHbMAhHWWqqokHk-OA",
  postCount: "VhY6XnydIgCYEfH0nja-5g",
  provider: "KYqx5DM-ZWPSmsYaTRc7Pg",
};

const CASHTAG_ONLY = /^\$([A-Za-z][A-Za-z0-9]{0,31})$/;

function symbolFor(advancedQuery) {
  const match = CASHTAG_ONLY.exec(String(advancedQuery ?? "").trim());
  return match ? match[1].toUpperCase() : null;
}

function dayIso(startTimeSec) {
  return new Date(startTimeSec * 1000).toISOString();
}

function windowTimes() {
  // Align with Radar: last ~7 days of Day buckets (UTC midnight).
  const now = Math.floor(Date.now() / 1000);
  const to_time = now;
  const from_time = now - 7 * 24 * 3600;
  return { from_time, to_time, granularity: "Day", timezone_offset: 0 };
}

function utcToday() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * A row still needs an official pull when the current UTC day has no x-radar
 * count. Having source=x-radar from older days is not enough — that is exactly
 * the failure mode that zeroed every TODAY cell after midnight.
 */
function needsOfficialBaseline(row, today = utcToday()) {
  const history = row?.x_signal?.history;
  if (!Array.isArray(history) || !history.length) return true;
  const day = history.find((h) => h.date === today);
  if (!day) return true;
  // expected_posts is set only from x_volume_baseline; 0 is a valid quiet day.
  if (day.posts_source === "x-radar" && day.expected_posts != null) return false;
  return day.expected_posts == null;
}

async function trackedSymbols() {
  if (process.env.SI_RADAR_SYMBOLS) {
    return process.env.SI_RADAR_SYMBOLS.split(",")
      .map((s) => s.trim().toUpperCase().replace(/USDT$/, ""))
      .filter(Boolean);
  }
  const response = await fetch(`${API_URL}/api/radar`);
  if (!response.ok) throw new Error(`radar list http ${response.status}`);
  const rows = await response.json();
  return rows.map((row) => String(row.symbol).toUpperCase());
}

async function staleTodaySymbols(symbols) {
  if (!STALE_HOURS || !Number.isFinite(STALE_HOURS) || STALE_HOURS <= 0) {
    return new Set();
  }
  const today = utcToday();
  const cutoff = Date.now() - STALE_HOURS * 3600 * 1000;
  const stale = new Set();
  // Sequential on purpose: keep load on the API light; N is the kanban size (~30).
  for (const symbol of symbols) {
    try {
      const response = await fetch(`${API_URL}/api/x-baseline/${symbol}?days=2`);
      if (!response.ok) {
        stale.add(symbol);
        continue;
      }
      const data = await response.json();
      const todayRow = (data.days || []).find((d) => d.date === today);
      if (!todayRow?.fetched_at) {
        stale.add(symbol);
        continue;
      }
      if (new Date(todayRow.fetched_at).getTime() < cutoff) stale.add(symbol);
    } catch {
      stale.add(symbol);
    }
  }
  return stale;
}

async function missingSymbols(all) {
  if (!ONLY_MISSING) return all;
  const response = await fetch(`${API_URL}/api/radar`);
  if (!response.ok) return all;
  const rows = await response.json();
  const today = utcToday();
  const need = new Set(
    rows.filter((r) => needsOfficialBaseline(r, today)).map((r) => String(r.symbol).toUpperCase()),
  );
  // Mid-day re-pull: current day is still filling in X Radar's series.
  const stale = await staleTodaySymbols(all);
  for (const s of stale) need.add(s);
  return all.filter((s) => need.has(s));
}

async function postBaseline(reports) {
  if (!reports.length) return { recorded: 0 };
  const response = await fetch(`${API_URL}/api/x-baseline`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(
      reports.map(({ symbol, query, source, days }) => ({ symbol, query, source, days })),
    ),
  });
  if (!response.ok) throw new Error(`x-baseline http ${response.status}`);
  return response.json();
}

const browser = await chromium.connectOverCDP(CDP_URL);
const context = browser.contexts()[0];
const page = await context.newPage();

/** @type {Record<string, string> | null} */
let authHeaders = null;

page.on("request", (req) => {
  if (!req.url().includes("/i/api/graphql/")) return;
  const h = req.headers();
  if (h.authorization && h["x-csrf-token"]) {
    authHeaders = {
      authorization: h.authorization,
      "x-csrf-token": h["x-csrf-token"],
      "x-twitter-auth-type": h["x-twitter-auth-type"] || "OAuth2Session",
      "x-twitter-active-user": h["x-twitter-active-user"] || "yes",
      "x-twitter-client-language": h["x-twitter-client-language"] || "en",
      "content-type": "application/json",
    };
  }
});

async function warmAuth() {
  await page.goto("https://x.com/home", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForTimeout(2_000);
  // Hit insights config so auth headers attach to GraphQL
  await page.goto("https://x.com/i/radar", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForTimeout(4_000);
  if (!authHeaders) {
    // force a config fetch from the page context after manual header seed
    await page.evaluate(async () => {
      await fetch("https://x.com/i/api/graphql/O6KEHm-xm_6YHA8QWfNS0g/insightsConfigQuery?variables=%7B%7D", {
        credentials: "include",
        headers: { "x-twitter-active-user": "yes", "x-twitter-auth-type": "OAuth2Session" },
      }).catch(() => null);
    });
    await page.waitForTimeout(1_000);
  }
  if (!authHeaders) throw new Error("could not capture GraphQL auth headers — is Chrome logged in?");
  if (page.url().includes("/i/flow/login")) {
    throw new Error("X session expired — re-run collector/x-session-import.mjs");
  }
}

async function gqlOnce(name, queryId, variables, method = "POST") {
  const headers = authHeaders;
  if (!headers) throw new Error("no auth headers");

  if (method === "GET") {
    const url =
      `https://x.com/i/api/graphql/${queryId}/${name}?variables=` +
      encodeURIComponent(JSON.stringify(variables));
    return page.evaluate(
      async ({ url, headers }) => {
        const response = await fetch(url, { method: "GET", credentials: "include", headers });
        const text = await response.text();
        let json = null;
        try {
          json = JSON.parse(text);
        } catch {
          /* ignore */
        }
        return { status: response.status, json, text: text.slice(0, 400) };
      },
      { url, headers },
    );
  }

  return page.evaluate(
    async ({ url, headers, body }) => {
      const response = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers,
        body,
      });
      const text = await response.text();
      let json = null;
      try {
        json = JSON.parse(text);
      } catch {
        /* ignore */
      }
      return { status: response.status, json, text: text.slice(0, 400) };
    },
    {
      url: `https://x.com/i/api/graphql/${queryId}/${name}`,
      headers,
      body: JSON.stringify({ variables, queryId }),
    },
  );
}

async function gql(name, queryId, variables, method = "POST") {
  // GraphQL is rate-limited; back off instead of failing the whole run.
  let delay = 5_000;
  for (let attempt = 0; attempt < 8; attempt++) {
    const result = await gqlOnce(name, queryId, variables, method);
    if (result.status !== 429) return result;
    console.log(JSON.stringify({
      event: "x_radar_rate_limited",
      name,
      attempt: attempt + 1,
      sleep_ms: delay,
    }));
    await page.waitForTimeout(delay);
    delay = Math.min(delay * 1.6, 90_000);
  }
  return gqlOnce(name, queryId, variables, method);
}

function rulesFromList(json) {
  const items = json?.data?.viewer_v2?.user_results?.result?.insight_rules?.items || [];
  return items.map((rule) => {
    const query = rule?.core?.advanced_query || "";
    const symbol = symbolFor(query);
    const rest_id = rule.rest_id || String(rule.id || "").split(":").pop();
    const counts = rule?.preview?.counts || rule?.matched_post_counts?.counts || [];
    return { symbol, query, rest_id, counts, rule };
  });
}

async function listRules(previews = true) {
  const result = await gql(
    "insightsListContextQuery",
    Q.list,
    { previewsEnabled: previews },
    "GET",
  );
  if (result.status !== 200 || !result.json) {
    throw new Error(`list rules failed http ${result.status}: ${result.text}`);
  }
  return rulesFromList(result.json);
}

async function deleteRule(restId) {
  const result = await gql("deleteInsightButtonMutation", Q.delete, { id: String(restId) }, "POST");
  const ok =
    result.status === 200 &&
    result.json?.data?.delete_insight_rule_v2?.__typename === "InsightRuleMutationSuccess";
  return { ok, status: result.status, body: result.text, json: result.json };
}

async function createRule(symbol) {
  const advanced_query = `$${symbol.toUpperCase()}`;
  const result = await gql(
    "createInsightInputMutation",
    Q.create,
    { tags: null, title: "", advanced_query, notifications_enabled: false },
    "POST",
  );
  const node = result.json?.data?.create_insight_rule_v2;
  if (node?.__typename === "InsightRuleFailure") {
    return {
      ok: false,
      error: `${node.error_code || "failure"}: ${node.error_message || ""}`.trim(),
      rest_id: null,
    };
  }
  const rest_id = node?.result?.rest_id;
  if (!rest_id) {
    return { ok: false, error: `create http ${result.status}: ${result.text}`, rest_id: null };
  }
  return { ok: true, rest_id: String(rest_id), query: advanced_query };
}

async function postCounts(restId) {
  const vars = { ...windowTimes(), id: String(restId) };
  // Counts can lag several seconds after create; also accept preview bars.
  for (let attempt = 0; attempt < 12; attempt++) {
    const result = await gql("usePostCountQuery", Q.postCount, vars, "GET");
    const rule = result.json?.data?.viewer_v2?.user_results?.result?.insight_rule_by_id;
    const matched = rule?.matched_post_counts;
    const counts = matched?.counts;
    if (Array.isArray(counts) && counts.length) {
      const query = rule?.core?.advanced_query || "";
      const symbol = symbolFor(query);
      return {
        ok: true,
        symbol,
        query,
        days: counts
          .filter((entry) => Number.isFinite(entry?.start_time))
          .map((entry) => ({
            day: dayIso(entry.start_time),
            post_count: Math.max(0, Number(entry.count) || 0),
          })),
      };
    }
    // Incomplete / still computing
    if (matched?.__typename && matched.__typename !== "InsightsMatchedPostCountsSuccess") {
      await page.waitForTimeout(2_000 + attempt * 500);
      continue;
    }
    await page.waitForTimeout(2_000 + attempt * 500);
  }

  // Fallback: list preview for this rest_id
  const listed = await listRules(true);
  const hit = listed.find((r) => String(r.rest_id) === String(restId));
  if (hit?.counts?.length) {
    return {
      ok: true,
      symbol: hit.symbol,
      query: hit.query,
      days: hit.counts
        .filter((entry) => Number.isFinite(entry?.start_time))
        .map((entry) => ({
          day: dayIso(entry.start_time),
          post_count: Math.max(0, Number(entry.count) || 0),
        })),
    };
  }
  return { ok: false, error: "no matched_post_counts" };
}

async function freeSlots(keepSet, targetFree = 1) {
  let rules = await listRules(false);
  const max = 5;
  const needDelete = Math.max(0, rules.length - (max - targetFree));
  if (needDelete <= 0 && rules.length < max) return rules;

  // Delete non-keep first, then oldest non-essential.
  const deletable = [
    ...rules.filter((r) => r.symbol && !keepSet.has(r.symbol)),
    ...rules.filter((r) => !r.symbol), // bare-word / unmapped
    ...rules.filter((r) => r.symbol && keepSet.has(r.symbol)),
  ];

  let deleted = 0;
  for (const rule of deletable) {
    if (rules.length - deleted <= max - targetFree) break;
    if (!rule.rest_id) continue;
    // Prefer not to delete KEEP until necessary
    if (keepSet.has(rule.symbol) && rules.length - deleted <= max - targetFree + keepSet.size) {
      continue;
    }
    const result = await deleteRule(rule.rest_id);
    if (result.ok) {
      deleted += 1;
      console.log(JSON.stringify({ event: "x_radar_slot_freed", symbol: rule.symbol, rest_id: rule.rest_id }));
    } else {
      console.log(JSON.stringify({ event: "x_radar_slot_free_failed", symbol: rule.symbol, detail: result.body?.slice(0, 160) }));
    }
    await page.waitForTimeout(400);
  }

  // If still full, force-delete anything except we need at least one free
  rules = await listRules(false);
  while (rules.length >= max) {
    const victim = rules.find((r) => !keepSet.has(r.symbol)) || rules[0];
    if (!victim?.rest_id) break;
    await deleteRule(victim.rest_id);
    console.log(JSON.stringify({ event: "x_radar_slot_force_freed", symbol: victim.symbol, rest_id: victim.rest_id }));
    await page.waitForTimeout(400);
    rules = await listRules(false);
  }
  return rules;
}

let exitCode = 0;
const reports = [];
const summary = { ok: [], failed: [], recorded: 0 };

try {
  let symbols = (await trackedSymbols()).slice(0, LIMIT);
  symbols = await missingSymbols(symbols);
  console.log(JSON.stringify({
    event: "x_radar_universe_start",
    symbols: symbols.length,
    keep: [...KEEP],
    mode: "graphql",
    only_missing: ONLY_MISSING,
  }));

  await warmAuth();

  // Harvest previews already on the board (may include KEEP).
  let rules = await listRules(true);
  for (const rule of rules) {
    if (!rule.symbol || !symbols.includes(rule.symbol)) continue;
    if (!rule.counts?.length) continue;
    const days = rule.counts
      .filter((entry) => Number.isFinite(entry?.start_time))
      .map((entry) => ({
        day: dayIso(entry.start_time),
        post_count: Math.max(0, Number(entry.count) || 0),
      }));
    if (!days.length) continue;
    const report = {
      symbol: rule.symbol,
      query: rule.query,
      source: "x-radar",
      days,
    };
    reports.push(report);
    summary.ok.push({
      symbol: rule.symbol,
      total: days.reduce((s, d) => s + d.post_count, 0),
      days: days.length,
      existing: true,
    });
  }

  // Ensure at least one free slot before rotating.
  await freeSlots(KEEP, 1);

  for (const symbol of symbols) {
    if (summary.ok.some((row) => row.symbol === symbol)) continue;

    try {
      await freeSlots(KEEP, 1);

      const created = await createRule(symbol);
      if (!created.ok) {
        // One more aggressive free + retry once
        await freeSlots(new Set(), 2);
        const retry = await createRule(symbol);
        if (!retry.ok) {
          summary.failed.push({ symbol, error: retry.error || created.error });
          console.log(JSON.stringify({ event: "x_radar_symbol_failed", symbol, error: retry.error || created.error }));
          continue;
        }
        Object.assign(created, retry);
      }

      const counts = await postCounts(created.rest_id);
      // Always try to free the slot after counting (unless KEEP)
      if (!KEEP.has(symbol)) {
        await deleteRule(created.rest_id);
      }

      if (!counts.ok || !counts.days?.length) {
        summary.failed.push({ symbol, error: counts.error || "no counts" });
        console.log(JSON.stringify({ event: "x_radar_symbol_failed", symbol, error: counts.error || "no counts" }));
        continue;
      }

      const report = {
        symbol: counts.symbol || symbol,
        query: counts.query || `$${symbol}`,
        source: "x-radar",
        days: counts.days,
      };
      reports.push(report);
      const total = counts.days.reduce((s, d) => s + d.post_count, 0);
      summary.ok.push({ symbol: report.symbol, total, days: counts.days.length });
      console.log(JSON.stringify({ event: "x_radar_symbol_ok", symbol: report.symbol, total, days: counts.days.length }));

      // Incremental persist so a mid-run crash keeps progress
      await postBaseline([report]);
      // Pace creates — Radar GraphQL 429s under burst traffic.
      await page.waitForTimeout(2_000);
    } catch (error) {
      summary.failed.push({ symbol, error: String(error.message).slice(0, 200) });
      console.log(JSON.stringify({ event: "x_radar_symbol_failed", symbol, error: String(error.message).slice(0, 200) }));
      await page.waitForTimeout(3_000);
    }
  }

  const result = await postBaseline(reports);
  summary.recorded = result.recorded ?? 0;
  summary.skipped_untracked = result.skipped_untracked ?? [];

  console.log(JSON.stringify({
    event: "x_radar_universe_complete",
    ok: summary.ok.length,
    failed: summary.failed.length,
    recorded: summary.recorded,
    symbols: summary.ok,
    errors: summary.failed,
    skipped_untracked: summary.skipped_untracked,
  }));
  if (summary.failed.length) exitCode = 2;
} catch (error) {
  console.error(JSON.stringify({ event: "x_radar_universe_error", message: String(error.message).slice(0, 300) }));
  exitCode = 1;
} finally {
  await page.close().catch(() => {});
  await browser.close().catch(() => {});
}
process.exit(exitCode);
