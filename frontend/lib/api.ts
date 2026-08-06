export type RadarRow = {
  symbol: string;
  canonical_pair: string;
  lana_phase: string;
  priority: number;
  source: string;
  crowd_state: string;
  state_confidence?: number;
  attention_score?: number;
  breadth_score?: number;
  authenticity_score?: number;
  coordination_score?: number;
  data_confidence: number;
  metrics?: Record<string, number>;
  contributions?: Record<string, Record<string, number>>;
  observed_at: string | null;
  x_signal?: {
    state: string; posts: number; posts_1h?: number | null;
    unique_authors: number | null; engagement: number | null;
    likes: number | null; replies: number | null; reposts: number | null; views: number | null;
    author_concentration: number | null; observed_at: string | null; stale: boolean;
    history_days?: number;
    // Present only in Radar mode, where X supplies daily counts and nothing else.
    source?: string; granularity?: string; metrics_note?: string; evidence_is_sample?: boolean;
    posts_today?: number; posts_yesterday?: number; median_daily?: number; acceleration?: number | null;
    coverage?: { scanned_hours: number; window_hours: number; ratio: number; saturated_slices: number; empty_slices: number; last_scan_at: string | null };
    capture?: { source: string; expected_posts: number | null; captured_posts: number | null; capture_ratio: number | null; days_compared: number; days_in_window: number };
    history: Array<{ date: string; posts: number; unique_authors: number; views: number; scanned_hours?: number; expected_hours?: number; posts_source?: string; harvested_posts?: number; expected_posts?: number | null; capture_ratio?: number | null }>;
    velocity: { posts_per_hour: number; authors_per_hour: number; baseline_per_hour: number; acceleration: number | null; percentile: number | null; history_days: number };
    evidence: Array<{ id: string; author: string; text: string; url: string | null; observed_at: string; likes: number; replies: number; reposts: number; views: number }>;
  };
};

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function getRadar(): Promise<RadarRow[]> {
  try {
    const response = await fetch(`${API_URL}/api/radar`, { cache: "no-store" });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return response.json();
  } catch {
    return [
      { symbol: "HOME", canonical_pair: "HOMEUSDT", lana_phase: "EXHAUSTION", priority: 0, source: "fixture", crowd_state: "NO_DATA", data_confidence: 0, observed_at: null },
      { symbol: "VIC", canonical_pair: "VICUSDT", lana_phase: "DUMP", priority: 0, source: "fixture", crowd_state: "NO_DATA", data_confidence: 0, observed_at: null },
      { symbol: "BULLA", canonical_pair: "BULLAUSDT", lana_phase: "DUMP", priority: 1, source: "repeat_offender", crowd_state: "NO_DATA", data_confidence: 0, observed_at: null }
    ];
  }
}
