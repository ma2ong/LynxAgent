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
  signal_pct_chg?: number | null
  change_since_signal?: number | null
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
  /** 提醒那一刻的当日涨跌幅；current_price / pct_chg / change_since_signal 由后端按请求现取 */
  signal_pct_chg?: number | null
  current_price?: number | null
  pct_chg?: number | null
  change_since_signal?: number | null
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
  score_floor?: number
  selection_note?: string
  live_price_as_of?: string
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
    // limit 只是安全上限：展示条数由后端的信号强度门槛决定，不按名次截断。
    // history_limit 取满：留痕要把当天上过榜的信号从早到晚全留住，截断只会砍掉早盘那一批。
    { limit: 200, history_limit: 500, refresh, _ts: Date.now() },
    { timeout: refresh ? 30000 : 15000 },
  )
  return (raw?.data || raw) as IntradaySignalPayload
}
