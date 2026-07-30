import { ApiClient } from './request'

export type IntradaySignalStatus = 'watch' | 'entry' | 'unbuyable' | 'invalid'

export interface IntradaySignalItem {
  symbol: string
  name: string
  industry: string
  status: IntradaySignalStatus
  status_label: string
  score: number
  current_price: number
  signal_price: number
  pct_chg: number
  entry_low: number
  entry_high: number
  chase_limit: number
  invalidation_price: number
  distance_to_limit: number
  activity_ratio: number
  feed_volume_ratio: number
  projected_amount_ratio: number
  range_position: number
  speed_1m: number
  breakout20: boolean
  breakout60: boolean
  sector_change: number
  market_median: number
  reasons: string[]
  phase: string
  phase_label: string
  first_seen: string
  triggered_at: string
  last_seen: string
  valid_until: string
  quote_source: string
  calibrated_probability: number | null
  signal_mode?: 'live' | 'intraday_archive' | 'close_review'
  actionable?: boolean
  reviewed_at?: string
}

export interface IntradaySignalEvent {
  event_id: string
  trade_date: string
  symbol: string
  name: string
  status: IntradaySignalStatus
  triggered_at: string
  signal_price: number
  score: number
  item?: IntradaySignalItem
}

export interface IntradaySignalPayload {
  status: 'waiting' | 'live' | 'closed' | 'degraded'
  as_of: string | null
  trade_date?: string
  phase: string
  phase_label: string
  universe?: number
  candidate_count: number
  entry_count: number
  watch_count: number
  unbuyable_count: number
  scan_interval_sec: number
  recommendation_limit?: number
  selection_note?: string
  market?: {
    tone: string
    median_pct: number
    breadth_up: number
  }
  items: IntradaySignalItem[]
  recent_events: IntradaySignalEvent[]
  method_note?: string
  probability_note?: string
  review_mode?: 'intraday_archive' | 'close_review'
  error?: string
}

export async function fetchIntradaySignals(refresh = false) {
  const raw = await ApiClient.get<any>(
    '/api/lite/intraday-signals',
    { limit: 10, history_limit: 100, refresh, _ts: Date.now() },
    { timeout: refresh ? 30000 : 15000 },
  )
  return (raw?.data || raw) as IntradaySignalPayload
}
