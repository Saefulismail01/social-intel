"use client";

import { useEffect, useMemo, useState } from "react";
import { API_URL, type RadarRow } from "../lib/api";
import { Glossary } from "./glossary";

const phases = ["ALL", "IGNITION", "SQUEEZE", "EXHAUSTION", "DUMP", "NORMAL"];
const states = ["ALL", "DORMANT", "SEEDING", "EMERGING", "BROADENING", "INSUFFICIENT_DATA", "STALE", "NO_DATA"];

type SourceHealth = {
  status: string; last_success: string | null; age_seconds?: number; source?: string;
  collector?: { status: string; last_seen_at: string | null; batches_observed: number; matched_posts: number };
  received_count?: number; inserted_count?: number; updated_count?: number; rejected_count?: number;
  recent_evidence?: Array<{ symbol: string; observed_at: string; author: string; text: string }>;
};

function score(value?: number) {
  return value == null ? "—" : value.toFixed(0);
}

// Post counts are only readable next to how much of the window was scanned:
// an hour nobody queried is not an hour with no posts.
function coverageLabel(coverage?: NonNullable<RadarRow["x_signal"]>["coverage"]) {
  if (!coverage || !coverage.window_hours) return "COVERAGE UNKNOWN";
  const percent = Math.round(coverage.ratio * 100);
  return `SCANNED ${coverage.scanned_hours}/${coverage.window_hours}H (${percent}%)`;
}

// X Radar's own daily counts are the denominator. Without it we can only say
// how many posts we hold, never how many we missed.
function captureLabel(capture?: NonNullable<RadarRow["x_signal"]>["capture"]) {
  if (!capture?.expected_posts || capture.capture_ratio == null) return "NO X BASELINE";
  return `CAPTURED ${capture.captured_posts}/${capture.expected_posts} (${Math.round(capture.capture_ratio * 100)}%)`;
}

function EvidenceList({ signal }: { signal: RadarRow["x_signal"] }) {
  const evidence = signal?.evidence ?? [];
  if (!evidence.length) return null;
  return <>
    {signal?.evidence_is_sample && <small>SAMPLE POSTS — NOT THE FULL SET</small>}
    {evidence.map(item => <a className="xEvidence" key={item.id} href={item.url ?? undefined} target="_blank" rel="noreferrer">
      <b>{item.author}</b><span>{item.text || "Public X post"}</span>
      <small>{item.likes} ♥ · {item.replies} replies · {item.reposts} reposts · {item.views} views</small>
    </a>)}
  </>;
}

function XSignalPanel({ signal }: { signal: RadarRow["x_signal"] }) {
  // Radar mode: X reports daily post counts and nothing else. Rendering the
  // per-hour velocity grid here would fill it with zeros that look like
  // measurements, so it is replaced by the daily figures X actually gives.
  if (signal?.source === "x-radar") {
    return <div className="xSignal">
      <small>X RADAR · OFFICIAL COUNTS · LAST {signal.history_days ?? 7}D</small>
      <strong>{signal.state}</strong>
      <span>{signal.posts} POSTS · DAILY GRANULARITY</span>
      <span>{signal.metrics_note}</span>
      <div className="velocityGrid radarGrid">
        <b>{signal.posts_today ?? 0}<small>TODAY</small></b>
        <b>{signal.posts_yesterday ?? 0}<small>YESTERDAY</small></b>
        <b>{signal.median_daily ?? 0}<small>7D MEDIAN/DAY</small></b>
        <b>{signal.acceleration == null ? "—" : `${signal.acceleration}×`}<small>VS MEDIAN</small></b>
        <b>{signal.history_days ?? 7}D<small>HISTORY</small></b>
      </div>
      <EvidenceList signal={signal} />
    </div>;
  }

  return <div className="xSignal">
    <small>GROK CLI · PUBLIC X · LAST {signal?.history_days ?? 7}D</small>
    <strong>{signal?.state ?? "NO_DATA"}</strong>
    <span>{signal?.posts ?? 0} POSTS · {signal?.unique_authors ?? 0} AUTHORS · {signal?.views ?? 0} VIEWS</span>
    <span>{signal?.posts_1h ?? 0} IN LAST 60M · {coverageLabel(signal?.coverage)} · {captureLabel(signal?.capture)}</span>
    <div className="velocityGrid">
      <b>{signal?.velocity?.posts_per_hour ?? 0}<small>POSTS/H NOW</small></b>
      <b>{signal?.velocity?.baseline_per_hour ?? 0}<small>7D MEDIAN/H</small></b>
      <b>{signal?.velocity?.acceleration == null ? "—" : `${signal.velocity.acceleration}×`}<small>ACCELERATION</small></b>
      <b>{signal?.velocity?.percentile == null ? "—" : `P${signal.velocity.percentile}`}<small>PERCENTILE</small></b>
      <b>{signal?.velocity?.authors_per_hour ?? 0}<small>AUTHORS/H</small></b>
      <b>{signal?.velocity?.history_days ?? 0}D<small>HISTORY</small></b>
    </div>
    <div className="xMetrics">
      <b>{signal?.likes ?? 0}<small>LIKES</small></b>
      <b>{signal?.replies ?? 0}<small>REPLIES</small></b>
      <b>{signal?.reposts ?? 0}<small>REPOSTS</small></b>
      <b>{score((signal?.author_concentration ?? 0) * 100)}%<small>TOP AUTHOR</small></b>
    </div>
    <EvidenceList signal={signal} />
  </div>;
}

function freshness(value: string | null) {
  if (!value) return "NO SOCIAL DATA";
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  return minutes < 1 ? "LIVE" : `${minutes}m ago`;
}

// One line, one metric: daily post count. A second "authors" line used to run
// alongside it with no indication that the two came from different sources
// (X Radar's official count vs. an incomplete harvested sample) — that made
// the chart unreadable. The source of the plotted count is now named in the
// header instead, and each point's own origin travels with its tooltip.
function SocialTrend({ history, source }: { history?: NonNullable<RadarRow["x_signal"]>["history"]; source?: string }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const points = history ?? [];
  const width = 560;
  const height = 160;
  const top = 14;
  const bottom = 28;
  const max = Math.max(1, ...points.map(point => point.posts));
  const x = (index: number) => points.length < 2 ? width / 2 : 12 + index * (width - 24) / (points.length - 1);
  const y = (value: number) => top + (height - top - bottom) * (1 - value / max);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.posts)}`).join(" ");
  const point = hovered != null ? points[hovered] : null;

  const sourceLabel = source === "x-radar" ? "X RADAR · OFFICIAL DAILY COUNTS"
    : source === "x-grok-cli" ? "HARVESTED SAMPLE · PARTIAL COVERAGE"
    : "NO X DATA SOURCE";

  return <section className="socialTrend">
    <div className="trendHeader"><div><small>SOCIAL TREND</small><strong>7 DAY POST VOLUME</strong></div><div className="trendLegend"><span className="posts">{sourceLabel}</span></div></div>
    {points.length ? <>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Seven day post volume trend" onMouseLeave={() => setHovered(null)}>
        <line className="trendGrid" x1="12" y1={y(max)} x2={width - 12} y2={y(max)} />
        <line className="trendGrid" x1="12" y1={y(0)} x2={width - 12} y2={y(0)} />
        {hovered != null && <line className="trendCursor" x1={x(hovered)} y1={top} x2={x(hovered)} y2={height - bottom} />}
        <path className="trendLine posts" d={path} />
        {points.map((item, index) => <g key={item.date}>
          <circle className="trendDot posts" cx={x(index)} cy={y(item.posts)} r={hovered === index ? 6 : 4} />
          <circle
            className="trendHit"
            cx={x(index)} cy={y(item.posts)} r="14"
            tabIndex={0}
            role="button"
            aria-label={`${item.date}: ${item.posts} posts`}
            onMouseEnter={() => setHovered(index)}
            onFocus={() => setHovered(index)}
            onBlur={() => setHovered(null)}
          />
          <text x={x(index)} y={height - 7} textAnchor="middle">{item.date.slice(5)}</text>
        </g>)}
      </svg>
      {point && <div className="trendTooltip" style={{ left: `${(x(hovered!) / width) * 100}%`, top: `${(y(point.posts) / height) * 100}%` }}>
        <b>{point.date}</b>
        <span>{point.posts} posts</span>
        {point.posts_source && <small>{point.posts_source === "x-radar" ? "OFFICIAL COUNT" : "HARVESTED SAMPLE"}</small>}
      </div>}
      <div className="trendTable" role="table" aria-label="Seven day post volume data">{points.map(item => <div role="row" key={item.date}><span>{item.date.slice(5)}</span><b>{item.posts}</b></div>)}</div>
    </> : <p>No seven-day social history available.</p>}
  </section>;
}

export function Radar({ initialRows }: { initialRows: RadarRow[] }) {
  const [data, setData] = useState(initialRows);
  const [health, setHealth] = useState<SourceHealth>({ status: "CONNECTING", last_success: null });
  const [phase, setPhase] = useState("ALL");
  const [state, setState] = useState("ALL");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(initialRows[0]?.symbol ?? "");
  const [detailTab, setDetailTab] = useState("OVERVIEW");
  const [detailExpanded, setDetailExpanded] = useState(false);
  const [showGlossary, setShowGlossary] = useState(false);

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const [radarResponse, healthResponse] = await Promise.all([
          fetch(`${API_URL}/api/radar`, { cache: "no-store" }),
          fetch(`${API_URL}/api/source-health`, { cache: "no-store" }),
        ]);
        if (!radarResponse.ok || !healthResponse.ok) throw new Error("api unavailable");
        if (active) {
          setData(await radarResponse.json());
          setHealth(await healthResponse.json());
        }
      } catch {
        if (active) setHealth(previous => ({ ...previous, status: "API_DOWN" }));
      }
    }
    refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const rows = useMemo(() => data.filter(row =>
    (phase === "ALL" || row.lana_phase === phase) &&
    (state === "ALL" || row.crowd_state === state) &&
    (!query || row.symbol.toLowerCase().includes(query.toLowerCase()))
  ), [data, phase, state, query]);
  const active = data.find(row => row.symbol === selected) ?? rows[0];
  const hasSquareData = (row: RadarRow) => Boolean(row.observed_at && row.crowd_state !== "NO_DATA");
  const apiLive = health.status !== "API_DOWN" && health.status !== "CONNECTING";
  const squareStatus = health.collector?.status ?? (health.source === "binance-square-browser" ? health.status : "NO COVERAGE");
  const xCoverage = data.filter(row => (row.x_signal?.posts ?? 0) > 0).length;

  if (showGlossary) return <Glossary onBack={() => setShowGlossary(false)} />;

  return <main><div className="appShell">
    <header className="topbar">
      <div className="brand"><span className="mark">SI</span><div><b>SOCIAL INTELLIGENCE</b><small>LANA CROWD TERMINAL</small></div></div>
      <div className="topbarActions"><div className="sourceStatus"><span className={apiLive ? "online" : "offline"}>● API {apiLive ? "ONLINE" : health.status}</span><span className={squareStatus === "LIVE" ? "online" : "warning"}>● SQUARE {squareStatus}</span><span className={xCoverage ? "online" : "warning"}>● X {xCoverage}/{data.length}</span></div><button className="glossaryButton" onClick={() => setShowGlossary(true)} aria-label="Open glossary">?</button></div>
    </header>

    <section className="summary">
      <div><label>TRACKED</label><strong>{data.length}</strong><small>active universe</small></div>
      <div><label>PRIORITY P0</label><strong>{data.filter(r => r.priority === 0).length}</strong><small>highest priority</small></div>
      <div><label>BROADENING</label><strong className="green">{data.filter(r => r.crowd_state === "BROADENING").length}</strong><small>wide participation</small></div>
      <div><label>COORDINATION</label><strong className="amber">{data.filter(r => (r.coordination_score ?? 0) >= 60).length}</strong><small>high-risk signals</small></div>
      <div><label>X COVERAGE</label><strong>{xCoverage}<em>/{data.length}</em></strong><small>tokens with evidence</small></div>
    </section>

    <section className="workspace">
      <div className="radarPanel">
        <div className="panelHeader"><div><b>CROWD RADAR</b><span>MARKET PHASE × SOCIAL PHASE</span></div><strong>{rows.length} RESULTS</strong></div>
        <div className="filters">
          <input className="tokenSearch" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search token…" aria-label="Search token" />
          <select value={phase} onChange={e => setPhase(e.target.value)} aria-label="Filter market phase">{phases.map(x => <option key={x}>{x}</option>)}</select>
          <select value={state} onChange={e => setState(e.target.value)} aria-label="Filter crowd state">{states.map(x => <option key={x}>{x}</option>)}</select>
          {(query || phase !== "ALL" || state !== "ALL") && <button className="resetButton" onClick={() => { setQuery(""); setPhase("ALL"); setState("ALL"); }}>RESET</button>}
        </div>
        <div className="tableWrap"><table>
          <thead><tr><th>TOKEN</th><th>LANA PHASE</th><th>CROWD STATE</th><th>ATTN</th><th>BREADTH</th><th>AUTH</th><th>COORD</th><th>CONF</th><th>FRESHNESS</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.symbol} onClick={() => setSelected(row.symbol)} className={active?.symbol === row.symbol ? "active" : ""}>
            <td><b>{row.symbol}</b><small>P{row.priority}</small></td>
            <td><span className={`pill ${row.lana_phase.toLowerCase()}`}>{row.lana_phase}</span></td>
            <td><span className={`state ${row.crowd_state.toLowerCase()}`}>{row.crowd_state}</span></td>
            <td>{hasSquareData(row) ? score(row.attention_score) : "—"}</td><td>{hasSquareData(row) ? score(row.breadth_score) : "—"}</td><td>{hasSquareData(row) ? score(row.authenticity_score) : "—"}</td>
            <td>{hasSquareData(row) ? score(row.coordination_score) : "—"}</td><td>{hasSquareData(row) ? score(row.data_confidence) : "—"}</td><td className={!row.observed_at ? "muted" : "green"}>{freshness(row.observed_at)}</td>
          </tr>)}</tbody>
        </table></div>
      </div>

      <aside className={detailExpanded ? "detailExpanded" : ""}>{active && <>
        <div className="tokenTitle"><div><small>SELECTED ASSET</small><h1>{active.symbol}<em>/USDT</em></h1></div><div className="detailActions"><span className={`pill ${active.lana_phase.toLowerCase()}`}>{active.lana_phase}</span><button onClick={() => setDetailExpanded(value => !value)}>{detailExpanded ? "COLLAPSE" : "EXPAND"}</button></div></div>
        <nav className="detailTabs">{["OVERVIEW", "SQUARE", "X EVIDENCE", "PROVENANCE"].map(tab => <button key={tab} className={detailTab === tab ? "active" : ""} onClick={() => setDetailTab(tab)}>{tab}</button>)}</nav>
        <div className="detailBody">
          {detailTab === "OVERVIEW" && <><div className="crowdState"><small>SQUARE CROWD STATE</small><strong>{active.crowd_state}</strong><span>CONFIDENCE {score((active.state_confidence ?? 0) * 100)}%</span></div><div className="crowdState"><small>X NARRATIVE · {active.x_signal?.history_days ?? 7}D{active.x_signal?.source === "x-radar" ? " · X RADAR" : ""}</small><strong className="amber">{active.x_signal?.state ?? "NO_DATA"}</strong><span>{active.x_signal?.source === "x-radar" ? `${active.x_signal.posts} POSTS · DAILY GRANULARITY` : `${active.x_signal?.posts ?? 0} POSTS · ${active.x_signal?.unique_authors ?? 0} AUTHORS · ${active.x_signal?.views ?? 0} VIEWS`}</span><span>{active.x_signal?.source === "x-radar" ? `OFFICIAL X COUNTS` : `${coverageLabel(active.x_signal?.coverage)} · ${captureLabel(active.x_signal?.capture)}`}</span></div><SocialTrend history={active.x_signal?.history} source={active.x_signal?.source} /><div className="scoreGrid">{[['ATTENTION', active.attention_score], ['BREADTH', active.breadth_score], ['AUTHENTICITY', active.authenticity_score], ['COORDINATION', active.coordination_score]].map(([label, value]) => <div key={label as string}><label>{label}</label><strong>{score(value as number | undefined)}</strong><progress max="100" value={value as number ?? 0} /></div>)}</div></>}
          {detailTab === "SQUARE" && <><div className="evidence"><b>SQUARE METRICS</b>{active.metrics ? Object.entries(active.metrics).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{typeof value === 'number' ? value.toFixed(2) : value}</strong></div>) : <p>No Square observations ingested. Market phase is live, but crowd conclusions are withheld.</p>}</div><div className="tape"><b>RECENT INGESTION TAPE</b>{health.recent_evidence?.slice(0, detailExpanded ? 20 : 8).map((item, index) => <div key={`${item.symbol}-${item.observed_at}-${index}`}><span>{item.symbol}</span><p><strong>{item.author || "Unknown"}</strong>{item.text || "Public Square post"}</p></div>) ?? <p>No matched Square evidence yet.</p>}</div></>}
          {detailTab === "X EVIDENCE" && <XSignalPanel signal={active.x_signal} />}
          {detailTab === "PROVENANCE" && <div className="provenance provenanceTab"><span>UNIVERSE SOURCE</span><b>{active.source}</b><span>SQUARE SCORE VERSION</span><b>{active.observed_at ? "crowd-v0.1.0" : "—"}</b><span>SQUARE OBSERVED</span><b>{active.observed_at ? freshness(active.observed_at) : "NO DATA"}</b><span>X OBSERVED</span><b>{active.x_signal?.observed_at ? freshness(active.x_signal.observed_at) : "NO DATA"}</b><span>X SOURCE</span><b>GROK CLI / PUBLIC X</b></div>}
        </div>
      </>}</aside>
    </section>
  </div></main>;
}
