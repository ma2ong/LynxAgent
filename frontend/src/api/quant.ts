import { ApiClient } from './request'

export interface QuantFactors {
  trend: number
  momentum: number
  rsi: number
  risk_control: number
  liquidity: number
}

export interface QuantRisk {
  volatility: number
  max_drawdown: number
  sharpe: number
}

export interface QuantAnalysisResult {
  symbol: string
  score: number
  signal: string
  factors: QuantFactors
  risk: QuantRisk
  latest: {
    date: string
    open: number
    high: number
    low: number
    close: number
    volume: number
    amount: number
  }
  warnings: string[]
  integrations?: {
    pattern_recognition?: PatternRecognitionResult
    kronos_forecast?: ForecastResult
    [key: string]: unknown
  }
}

export interface QuantPick {
  symbol: string
  score: number
  signal: string
  factors: QuantFactors
  risk: QuantRisk
}

export interface QuantScreenResult {
  total: number
  items: QuantPick[]
  errors: Record<string, string>
}

export interface TradePlan {
  buy_price: number
  stop_loss: number
  take_profit: number
  stop_loss_pct: number
  take_profit_pct: number
  risk_reward_ratio: number | null
  atr: number | null
  basis: 'atr' | 'pct'
}

export interface QuantSmartPoolItem {
  symbol: string
  code: string
  name: string
  market: string
  industry?: string
  board?: string
  score: number
  quant_score: number
  /** 原日K结构分保留不变；盘中动态分和时机分只用于当前推荐排序 */
  quality_score?: number
  realtime_rank_score?: number
  intraday_strength_score?: number | null
  intraday_activity_percentile?: number | null
  timing_score?: number | null
  timing_status?: 'confirmed' | 'watch' | 'unconfirmed' | 'blocked' | 'hot_limit' | 'pending'
  timing_label?: string
  timing_adjustment?: number
  timing_actionable?: boolean
  timing_signal_mode?: 'live' | 'intraday_archive' | 'close_review' | null
  timing_reasons?: string[]
  /** 今日首次上榜时的价格与时刻，以及从那时到现在的涨跌幅 */
  first_price?: number | null
  first_at?: string
  since_first_pct?: number | null
  distance_to_limit?: number
  radar_signal?: {
    status?: string
    score?: number | null
    entry_low?: number
    entry_high?: number
    chase_limit?: number
    invalidation_price?: number
    valid_until?: string
    actionable?: boolean
  }
  ai_factor_score?: number
  ai_factor_rank?: number | null
  ai_factor_source?: string
  signal: string
  close?: number
  pct_chg?: number | null
  amount?: number
  factors: QuantFactors
  risk: QuantRisk
  reasons: string[]
  patterns?: PatternRecognitionResult['patterns']
  forecast?: ForecastResult
  trade_plan?: TradePlan
  /** 当前已封涨停：展示的收盘买入价实际买不到，实盘只能次日开盘入场 */
  limit_up?: boolean
  /** 七不买体检命中的风险/提示项 */
  risk_flags?: RiskFlag[]
  /** ①c 双确认：结构因子 + 低位形态；triple 再叠相对强度 */
  dual_confirm?: boolean
  triple_confirm?: boolean
  confluence_tags?: string[]
}

export interface RiskFlag {
  key: string
  name: string
  level: 'risk' | 'info'
  reason: string
}

export interface DecisionAngle {
  key: string
  label: string
  score: number
  state: string
  note: string
}

export interface RiskCheckResult {
  symbol: string
  name: string
  flags: RiskFlag[]
  risk_count: number
  advice: string
  // 多角度避雷决策（新版 /risk-check 返回；旧字段保留向后兼容）
  composite?: number
  verdict?: { level: string; stance: string }
  angles?: DecisionAngle[]
  market_env?: string
  disclaimer?: string
}

export interface QuantSmartPoolResult {
  strategy?: string
  source: string
  universe_size: number
  analyzed?: number
  daily_as_of?: string
  realtime_as_of?: string
  realtime_status?: 'live' | 'partial' | 'snapshot' | 'unavailable'
  realtime_market_phase?: string
  realtime_quote_count?: number
  realtime_quote_total?: number
  realtime_coverage?: number
  ranking_basis?: string
  force_refreshed?: boolean
  requested_limit?: number
  score_floor?: number
  score_floor_best?: number
  score_floor_note?: string
  score_floor_fallback?: boolean
  ai_factor?: {
    status?: string
    pick_date?: string
    universe?: number
  }
  position_gate?: {
    state?: string
    temp?: number
    coefficient?: number
    max_single_position_pct?: number
    label?: string
    note?: string
  }
  intraday_candidate_count?: number
  // 名单基准：完整日K冻结结构底池，盘中实时量价动态重排最终名单
  list_basis?: {
    as_of?: string
    frozen?: boolean
    structure_frozen?: boolean
    intraday_dynamic?: boolean
    candidate_count?: number
    prev_date?: string | null
    same_count?: number
    total?: number
  }
  dual_confirm_count?: number
  triple_confirm_count?: number
  excluded_severe_count?: number
  excluded_severe_samples?: Array<{ name?: string; symbol?: string; reason?: string }>
  timing_confirmed_count?: number
  timing_watch_count?: number
  timing_wait_count?: number
  timing_actionable_count?: number
  timing_excluded_count?: number
  timing_excluded_samples?: Array<{ name?: string; symbol?: string; reason?: string }>
  industry_concentration?: {
    top_industry?: string | null
    top_count?: number
    top_share?: number
    warning?: boolean
    note?: string
  }
  timing_gate?: {
    status?: string
    is_current?: boolean
    as_of?: string
    trade_date?: string
    phase?: string
    phase_label?: string
    review_mode?: 'intraday_archive' | 'close_review'
    candidate_count?: number
  }
  items: QuantSmartPoolItem[]
  errors?: Record<string, string>
}

export interface QuantSmartPoolTask {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  phase: string
  message: string
  limit: number
  universe_limit: number
  created_at?: string
  updated_at?: string
  finished_at?: string
  error?: string
  result?: QuantSmartPoolResult
}

export interface QuantPatternPoolItem extends QuantSmartPoolItem {
  pattern_score: number
  matched_patterns?: PatternRecognitionResult['patterns']
}

export interface QuantPatternPoolResult {
  source: string
  universe_size: number
  analyzed?: number
  matched?: number
  items: QuantPatternPoolItem[]
  errors?: Record<string, string>
  pattern_model?: string[]
  excluded?: number
  excluded_reasons?: Record<string, number>
  scanned?: number
}

export interface QuantPatternPoolTask {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  phase: string
  message: string
  limit: number
  universe_limit: number
  created_at?: string
  updated_at?: string
  finished_at?: string
  error?: string
  result?: QuantPatternPoolResult
}

export interface QuantDataHealth {
  status: string
  ready: boolean
  meta_count: number
  kline_symbols: number
  latest_date: string
  latest_date_count: number
  today: string
  today_count: number
  complete_threshold: number
  latest_complete_date: string
  latest_complete_count: number
  today_complete: boolean
  needs_incremental_sync: boolean
  gap_dates?: string[]
  recent_days?: { date: string; count: number }[]
  message: string
  sync_running?: boolean
  sync_phase?: string
  sync_done?: number
  sync_total?: number
  sync_errors_count?: number
  last_full_sync?: string
  last_incremental_sync?: string
  auto_started?: boolean
}

export interface QuantSourceHealth {
  grade: string
  message: string
  active_count: number
  primary: string
  active_order: string[]
  fallback_enabled: boolean
  sources: Array<{
    key: string
    name: string
    installed: boolean
    enabled: boolean
    priority: number
    capabilities: string[]
    notes: string
  }>
  local: QuantDataHealth
  policy: Array<{ step: number; name: string; role: string }>
  sync: Record<string, any>
}

export interface BacktestResult {
  symbol: string
  strategy: string
  engine: string
  start_date: string | null
  end_date: string | null
  initial_cash: number
  final_value: number
  total_return: number
  annualized_return: number
  max_drawdown: number
  win_rate: number
  trades: number
  equity_curve: Array<{
    date: string
    equity: number
    position: number
  }>
}

export interface QuantStockPoolResult {
  source: string
  total: number
  items: Array<{
    symbol: string
    name?: string
    market?: string
    source?: string
    updated_at?: string
  }>
}

export interface DataLakeSyncResult {
  source: string
  collection: string
  total: number
  inserted: number
  updated: number
  errors: string[]
}

export interface FactorResearchResult {
  universe_size: number
  candidates: Array<{
    name: string
    hypothesis: string
    score: number
    avg_return: number
    avg_max_drawdown: number
    avg_win_rate: number
    sample_size: number
    runs: BacktestResult[]
  }>
  errors: Record<string, string>
}

export interface ForecastResult {
  symbol: string
  engine: string
  horizon: number
  trend_score: number
  upside_probability: number
  expected_return: number
  expected_drawdown: number
  path: Array<{
    date: string
    close: number
    low: number
    high: number
  }>
  warnings: string[]
}

export interface PatternRecognitionResult {
  symbol: string
  source: string
  patterns: Array<{
    key: string
    name: string
    active: boolean
    strength: number
    reason: string
  }>
}

export interface IntegrationCapability {
  name: string
  project: string
  status: string
  capabilities: string[]
  integration_mode: string
  notes: string
}

export interface QuantCapabilitiesResult {
  strategies: string[]
  integrations: IntegrationCapability[]
}

const unwrap = <T>(response: any): T => {
  if (response && typeof response === 'object' && 'data' in response && 'success' in response) {
    return response.data as T
  }
  return response as T
}

const nonce = () => Date.now()

const normalizeSmartPoolResult = (raw: any): QuantSmartPoolResult => {
  const items = (raw?.items || []).map((item: any) => ({
    ...item,
    symbol: item.symbol || item.code,
    code: item.code || item.symbol,
    score: Number(item.score ?? item.quant_score ?? item.smart_score ?? 0),
    quant_score: Number(item.quant_score ?? item.smart_score ?? item.score ?? 0),
    market: item.market || 'A股',
    industry: item.industry || item.board || '',
    board: item.board || item.industry || ''
  }))
  return {
    strategy: raw?.strategy,
    source: raw?.source || 'lite-smart-pool',
    universe_size: raw?.universe_size || items.length,
    analyzed: raw?.analyzed || raw?.universe_size || items.length,
    ai_factor: raw?.ai_factor,
    daily_as_of: raw?.daily_as_of,
    realtime_as_of: raw?.realtime_as_of,
    realtime_status: raw?.realtime_status,
    realtime_market_phase: raw?.realtime_market_phase,
    realtime_quote_count: raw?.realtime_quote_count,
    realtime_quote_total: raw?.realtime_quote_total,
    realtime_coverage: raw?.realtime_coverage,
    ranking_basis: raw?.ranking_basis,
    force_refreshed: raw?.force_refreshed,
    requested_limit: raw?.requested_limit,
    score_floor: raw?.score_floor,
    score_floor_best: raw?.score_floor_best,
    score_floor_note: raw?.score_floor_note,
    score_floor_fallback: raw?.score_floor_fallback,
    intraday_candidate_count: raw?.intraday_candidate_count,
    position_gate: raw?.position_gate,
    list_basis: raw?.list_basis,
    dual_confirm_count: raw?.dual_confirm_count,
    triple_confirm_count: raw?.triple_confirm_count,
    excluded_severe_count: raw?.excluded_severe_count,
    excluded_severe_samples: raw?.excluded_severe_samples,
    timing_confirmed_count: raw?.timing_confirmed_count,
    timing_watch_count: raw?.timing_watch_count,
    timing_wait_count: raw?.timing_wait_count,
    timing_actionable_count: raw?.timing_actionable_count,
    timing_excluded_count: raw?.timing_excluded_count,
    timing_excluded_samples: raw?.timing_excluded_samples,
    industry_concentration: raw?.industry_concentration,
    timing_gate: raw?.timing_gate,
    items,
    errors: raw?.errors || {}
  }
}

const normalizePatternPoolResult = (raw: any): QuantPatternPoolResult => {
  const items = (raw?.items || []).map((item: any) => ({
    ...item,
    symbol: item.symbol || item.code,
    code: item.code || item.symbol,
    score: Number(item.score ?? item.quant_score ?? item.pattern_score ?? 0),
    quant_score: Number(item.quant_score ?? item.score ?? item.pattern_score ?? 0),
    pattern_score: Number(item.pattern_score ?? item.score ?? 0),
    market: item.market || 'A股',
    industry: item.industry || item.board || '',
    board: item.board || item.industry || '',
    patterns: item.patterns || item.matched_patterns || [],
    matched_patterns: item.matched_patterns || item.patterns || [],
  }))
  return {
    source: raw?.source || 'pre-lift-pattern-pool',
    universe_size: raw?.universe_size || items.length,
    analyzed: raw?.analyzed || raw?.universe_size || items.length,
    matched: raw?.matched || items.length,
    items,
    errors: raw?.errors || {},
    pattern_model: raw?.pattern_model || [],
    excluded: raw?.excluded,
    excluded_reasons: raw?.excluded_reasons,
    scanned: raw?.scanned,
  }
}

export const quantApi = {
  capabilities: async () =>
    unwrap<QuantCapabilitiesResult>(await ApiClient.get('/api/quant/capabilities', undefined, { timeout: 60000 })),

  smartPool: async (limit = 10, universeLimit = 300, cacheOnly = false) => {
    const raw = unwrap<any>(await ApiClient.get('/api/lite/smart-pool', { limit, universe_limit: universeLimit, cache_only: cacheOnly, _ts: nonce() }, { timeout: cacheOnly ? 20000 : 300000 }))
    return normalizeSmartPoolResult(raw)
  },

  startSmartPoolTask: async (limit = 10, universeLimit = 300, strategy = 'balanced') =>
    unwrap<QuantSmartPoolTask>(await ApiClient.post('/api/lite/smart-pool/tasks', undefined, {
      params: { limit, universe_limit: universeLimit, strategy, force_refresh: false, _ts: nonce() },
      timeout: 15000
    })),

  smartPoolTask: async (taskId: string) => {
    const task = unwrap<any>(await ApiClient.get(`/api/lite/smart-pool/tasks/${taskId}`, { _ts: nonce() }, { timeout: 15000 }))
    if (task?.result) {
      task.result = normalizeSmartPoolResult(unwrap<any>(task.result))
    }
    return task as QuantSmartPoolTask
  },

  // 形态扫描：后台任务 + 轮询（全市场约 90 秒，同步请求会被反向代理掐断，也会让用户干等）
  startPatternPoolTask: async (limit = 20, universeLimit = 5000, minStrength = 70, excludeFundamental = true) =>
    unwrap<QuantPatternPoolTask>(await ApiClient.post('/api/lite/pattern-pool/tasks', undefined, {
      params: {
        limit, universe_limit: universeLimit, min_strength: minStrength,
        exclude_fundamental: excludeFundamental, _ts: nonce(),
      },
      timeout: 15000,
    })),

  patternPoolTask: async (taskId: string) => {
    const task = unwrap<any>(await ApiClient.get(`/api/lite/pattern-pool/tasks/${taskId}`, { _ts: nonce() }, { timeout: 15000 }))
    if (task?.result) {
      task.result = normalizePatternPoolResult(unwrap<any>(task.result))
    }
    return task as QuantPatternPoolTask
  },


  analyze: async (payload: { symbol: string; start_date?: string; end_date?: string }) =>
    unwrap<QuantAnalysisResult>(await ApiClient.post('/api/quant/analyze', payload, { timeout: 120000 })),

  forecast: async (payload: { symbol: string; start_date?: string; end_date?: string; horizon?: number }) =>
    unwrap<ForecastResult>(await ApiClient.post('/api/quant/forecast', payload, { timeout: 120000 })),

  patterns: async (payload: { symbol: string; start_date?: string; end_date?: string }) =>
    unwrap<PatternRecognitionResult>(await ApiClient.post('/api/quant/patterns', payload, { timeout: 120000 })),

  screen: async (payload: { symbols: string[]; start_date?: string; end_date?: string; limit?: number }) =>
    unwrap<QuantScreenResult>(await ApiClient.post('/api/quant/screen', payload, { timeout: 180000 })),

  backtest: async (payload: {
    symbol: string
    strategy?: string
    strategies?: string[]
    combine?: string
    stop_loss_pct?: number
    engine?: string
    start_date?: string
    end_date?: string
    initial_cash?: number
  }) => unwrap<BacktestResult>(await ApiClient.post('/api/quant/backtest', payload, { timeout: 180000 })),

  pool: async (limit = 200) =>
    unwrap<QuantStockPoolResult>(await ApiClient.get('/api/quant/pool', { limit }, { timeout: 120000 })),

  syncDataLake: async (payload: { limit?: number }) =>
    unwrap<DataLakeSyncResult>(await ApiClient.post('/api/quant/datalake/sync', payload, { timeout: 180000 })),

  research: async (payload: { symbols: string[]; start_date?: string; end_date?: string; initial_cash?: number }) =>
    unwrap<FactorResearchResult>(await ApiClient.post('/api/quant/research', payload, { timeout: 240000 })),

  syncMarket: async (full = false) =>
    unwrap<any>(await ApiClient.post('/api/lite/datalake/sync', {}, { params: { full, _ts: nonce() }, timeout: 30000 })),

  syncStatus: async () =>
    unwrap<any>(await ApiClient.get('/api/lite/datalake/sync/status', { _ts: nonce() }, { timeout: 15000 })),

  dataHealth: async (autoStart = true) =>
    unwrap<QuantDataHealth>(await ApiClient.get('/api/lite/datalake/health', { auto_start: autoStart, _ts: nonce() }, { timeout: 20000 })),

  sourceHealth: async () =>
    unwrap<QuantSourceHealth>(await ApiClient.get('/api/lite/datalake/sources/health', { _ts: nonce() }, { timeout: 20000 })),

  klineDetail: async (symbol: string, name = '', days = 250) =>
    unwrap<any>(await ApiClient.get('/api/quant/kline', { symbol, name, days }, { timeout: 60000 })),

  // ---- 七不买体检：规则化风险检查（日线 + 实时行情，非 LLM）----
  riskCheck: async (symbol: string) =>
    unwrap<RiskCheckResult>(await ApiClient.get('/api/quant/risk-check', { symbol, _ts: nonce() }, { timeout: 30000 })),

  // ---- 个股深研增强：投资者画像 / 红旗（走 LLM）----
  investorPanel: async (symbol: string) =>
    unwrap<any>(await ApiClient.get('/api/quant/stock/investor-panel', { symbol }, { timeout: 120000 })),
  redFlags: async (symbol: string) =>
    unwrap<any>(await ApiClient.get('/api/quant/stock/red-flags', { symbol }, { timeout: 120000 })),

  factorModel: async (params: {
    universe_limit?: number
    horizon?: number
    k?: number
    mode?: 'rolling' | 'once'
    neutralize?: boolean
    retrain_every?: number
    force?: boolean
  } = {}) =>
    unwrap<MLFactorResult>(await ApiClient.get('/api/quant/ml/factor-model', params, { timeout: 300000 })),

  serenityEvents: async (force = false, maxNews = 30) =>
    unwrap<SerenityEventsResult>(await ApiClient.get('/api/quant/serenity/events', { force, max_news: maxNews }, { timeout: 300000 })),

  serenityDeep: async (payload: { theme: string; event?: string; beneficiaries?: any[] }) =>
    unwrap<any>(await ApiClient.post('/api/quant/serenity/deep', payload, { timeout: 180000 })),

  picksStats: async (days = 30, pool = '', includeItems = true) =>
    unwrap<PicksStatsResult>(await ApiClient.get(
      '/api/quant/picks/stats',
      { days, pool, include_items: includeItems, _ts: nonce() },
      { timeout: 120000 },
    )),

  marketContext: async () =>
    unwrap<MarketContext>(await ApiClient.get('/api/quant/market-context', { _ts: nonce() }, { timeout: 30000 })),

  riskAlert: async () =>
    unwrap<RiskAlert>(await ApiClient.get('/api/quant/risk-alert', { _ts: nonce() }, { timeout: 30000 })),

  riskScan: async (limit = 200) =>
    unwrap<RiskScan>(await ApiClient.get('/api/quant/risk-scan', { limit, _ts: nonce() }, { timeout: 60000 })),

  replayRun: async (months = 12, step = 5, topN = 20) =>
    unwrap<ReplayStatus>(await ApiClient.post(`/api/quant/replay/run?months=${months}&step=${step}&top_n=${topN}`, undefined, { timeout: 30000 })),

  replayStatus: async () =>
    unwrap<ReplayStatus>(await ApiClient.get('/api/quant/replay/status', { _ts: nonce() }, { timeout: 15000 })),

  replayResults: async () =>
    unwrap<ReplaySummary>(await ApiClient.get('/api/quant/replay/results', { _ts: nonce() }, { timeout: 60000 })),

  signalStats: async (pool: string, days = 90) =>
    unwrap<SignalStats>(await ApiClient.get('/api/quant/signal-stats', { pool, days, _ts: nonce() }, { timeout: 120000 })),
}

export interface MarketContext {
  state?: '偏暖' | '中性' | '偏冷'
  /** 逐日加权温度分 0-100（50=中性），state 由它分档 */
  temp?: number
  /** 逐日中位/广度序列，最新一日在前 */
  daily?: Array<{ date: string; median_pct: number; breadth_up: number; weighted_pct?: number | null; count: number }>
  median_5d_pct?: number
  breadth_up?: number
  latest_day?: {
    median_pct?: number
    breadth_up?: number
    /** 成交额加权涨幅：钱实际赚没赚到，与等权中位背离时说明权重股在杀跌 */
    weighted_pct?: number | null
    label?: string
    rebound?: boolean
  }
  /** 指数口径：用户口中的「大盘涨没涨」 */
  index?: {
    items?: Array<{ code: string; name: string; date: string; last_pct: number; window_pct: number }>
    last_pct?: number | null
  }
  /** 指数与个股背离的一句话说明（无背离为空串） */
  divergence?: string
  as_of?: string
  /** true=最新一日来自实时快照；false=截至 as_of 收盘日线 */
  intraday?: boolean
  /** 实时快照的行情时刻 HH:MM（intraday 时有值） */
  as_of_time?: string
  advice?: string
}

export interface RiskSignal {
  key: string
  name: string
  value: number
  risk: number
  detail: string
}
export interface RiskAlert {
  score: number
  level: '安全' | '警惕' | '危险' | '极危'
  action: string
  signals: RiskSignal[]
  history_anchor?: string
  disclaimer?: string
  market_state?: string
  as_of?: string
  intraday?: boolean
}
export interface RiskScanItem {
  symbol: string
  name: string
  pct: number
  close: number
  current_price?: number | null
  current_pct?: number | null
  severity: number
  signal: '退出/止损' | '减仓防守' | '反包观察' | '持有观察'
  layer: 'new_breakdown' | 'confirmed_breakdown' | 'persistent_weakness' | 'trouble'
  reason: string
  confidence?: number
  risk_score?: number
  risk_dimensions?: string[]
  risk_factors?: string[]
  protect_factors?: string[]
  context_factors?: string[]
  amount_yi: number
  amount_ratio?: number
  capital_flow_5d?: number
  relative_pct?: number
}
export interface RiskScan {
  total_flagged: number
  breakdown_count: number
  urgent_count?: number
  actionable_count?: number
  layer_counts?: Record<string, number>
  recommendation_counts?: Record<'exit' | 'reduce' | 'rebound' | 'watch', number>
  market_context?: {
    median_pct: number
    up_share: number
    breakdown_share: number
    broad_retreat: boolean
  }
  method_note?: string
  universe?: number
  as_of?: string
  items: RiskScanItem[]
}

export interface PicksHorizonStat {
  samples: number
  win_rate: number | null
  avg_return: number | null
  excess_win_rate?: number | null
  avg_excess?: number | null
}

export interface PicksAlignedStat {
  samples: number
  excess_win_rate?: number | null
  avg_excess?: number | null
}

export interface PicksPoolStat {
  pool: string
  picks: number
  /** 该池在窗口内的首个留痕日；各池上线时间不同，横向比必须看这个 */
  first_pick_date?: string | null
  horizons: { t1: PicksHorizonStat; t3: PicksHorizonStat; t5: PicksHorizonStat }
  /** 统一起点（aligned_since）后的超额，唯一可跨池横向比较的口径 */
  aligned?: { t1: PicksAlignedStat; t3: PicksAlignedStat; t5: PicksAlignedStat }
}

export interface PicksStatsItem {
  pick_date: string
  pool: string
  symbol: string
  name: string
  score: number
  rank: number
  base_close: number
  t1: number | null
  t3: number | null
  t5: number | null
  excess_t1?: number | null
  excess_t3?: number | null
  excess_t5?: number | null
}

export interface LatestPickItem {
  pick_date: string
  batch_at: string
  pool: string
  symbol: string
  name: string
  score: number
  close: number
  rank: number
  patterns: string
}

export interface PicksStatsResult {
  days: number
  since: string
  /** 各池共同起点：仍在运行的池里最晚的那个首痕日 */
  aligned_since?: string | null
  total_picks: number
  pools: PicksPoolStat[]
  items: PicksStatsItem[]
  latest: LatestPickItem[]
}

export interface ReplayStatus {
  running: boolean
  started?: boolean
  reason?: string
  run_id?: string
  phase?: string
  done?: number
  total?: number
}

export interface ReplayMonthly {
  month: string
  picks: number
  excess_win_rate: number
  avg_excess: number
}

export interface ReplayCurvePoint {
  as_of: string
  avg_excess: number
  cum_excess: number
}

export interface ReplayOpenEntry {
  evaluated: number
  excess_win_rate: number | null
  avg_excess: number | null
  median_excess: number | null
}

export interface ReplayRegimeRow {
  regime: string
  sessions: number
  picks: number
  excess_win_rate: number
  avg_excess: number
  median_excess: number
  avg_excess_open: number | null
}

export interface ReplayPoolSummary {
  pool: string
  picks: number
  evaluated: number
  win_rate: number | null
  avg_return: number | null
  excess_win_rate: number | null
  avg_excess: number | null
  median_excess?: number | null
  p10_excess?: number | null
  p90_excess?: number | null
  limitup_ratio?: number | null
  open_entry?: ReplayOpenEntry
  regimes?: ReplayRegimeRow[]
  monthly: ReplayMonthly[]
  curve: ReplayCurvePoint[]
}

export interface ReplaySummary {
  run_id?: string
  created_at?: string
  params?: { months?: number; step?: number; top_n?: number; sessions?: number }
  pools?: ReplayPoolSummary[]
}

export interface SignalPatternStat {
  name: string
  samples: number
  excess_win_rate: number
  avg_excess: number
}

export interface SignalStats {
  pool: string
  days: number
  live: { t1: PicksHorizonStat; t3: PicksHorizonStat; t5: PicksHorizonStat } | null
  live_picks: number
  patterns: SignalPatternStat[]
  replay: {
    picks: number
    evaluated: number
    excess_win_rate: number | null
    avg_excess: number | null
    run_id?: string
    created_at?: string
  } | null
}

export interface MLFactorPick {
  symbol: string
  name: string
  score: number
}

export interface MLFactorMetrics {
  total_return: number
  annual_return: number
  sharpe: number
  max_drawdown: number
  win_rate: number
  n_periods: number
}

export interface MLFactorResult {
  status?: 'ready' | 'computing' | 'error'
  elapsed_sec?: number
  mode: string
  universe: number
  horizon: number
  k: number
  neutralized: boolean
  n_models?: number
  ic: { rank_ic_mean: number; rank_icir: number; ic_mean: number; n_days: number }
  metrics: { topk: MLFactorMetrics; benchmark: MLFactorMetrics; long_short: MLFactorMetrics }
  pick_date: string
  picks: MLFactorPick[]
  top_features: Record<string, number>
  curves: { dates: string[]; topk: number[]; benchmark: number[]; long_short: number[] }
  cached: boolean
  age_sec?: number
  generated_at: number
  error?: string
}

export interface SerenityBeneficiary { symbol: string; name: string; why?: string }
export interface SerenityEvent {
  event: string
  theme: string
  thesis: string
  evidence?: string
  evidence_tier?: string
  stage?: string
  significance?: number
  scores?: Record<string, number>
  beneficiaries: SerenityBeneficiary[]
  validation: string
  falsification: string
  source_url: string
  ts: number
}
export interface SerenityEventsResult {
  status: 'ready' | 'computing'
  events?: SerenityEvent[]
  count?: number
  cached?: boolean
  age_sec?: number
  elapsed_sec?: number
}

export interface MacroIndexQuote {
  code: string
  name: string
  price: number
  change: number | null
  change_percent: number | null
  amount_wan: number | null
}

export interface MacroBarData {
  indices: MacroIndexQuote[]
  breadth: { up: number; down: number; flat: number; amount_yi: number } | null
  updated_at: string
}

export const macroBarApi = {
  fetch: async () => {
    const raw = await ApiClient.get<{ success: boolean; data: MacroBarData }>('/api/lite/macro-bar', undefined, { timeout: 15000 })
    return (raw as any)?.data as MacroBarData | null
  },
}

export interface DailyReportSection { title: string; body: string }
export interface DailyReport {
  kind: 'premarket' | 'close'
  date: string
  generated_at: string
  llm: boolean
  sections: DailyReportSection[]
}


export interface PanelVerdict {
  persona: string
  style: string
  score: number
  stance: string
  reason: string
  /** 该派的数据是否齐全；缺数据时按中性 50 计并在 summary 里说明 */
  available?: boolean
}

export interface PanelScore {
  consensus_score: number
  divergence: number
  bull_count: number
  bear_count: number
  verdicts: PanelVerdict[]
  summary: string
  /** 'rules'（2026-08-19 起）/ 'llm'（之前）。两个时期的分数不可比。 */
  method?: string
}

export interface PanelBatchData {
  date: string
  pool: string
  items: Record<string, PanelScore>
  pending: number
  message?: string
}

export const panelApi = {
  batch: async (pool: string) => {
    const raw = await ApiClient.get<any>('/api/quant/panel/batch', { pool }, { timeout: 30000 })
    return (raw as any)?.data as PanelBatchData | null
  },
}

export interface HeatmapItem {
  name: string
  pct: number
  value: number
  amount_yi?: number
  count?: number
  symbol?: string
  mv_yi?: number
  /** 多周期涨跌幅%；缺 bar 的次新股/长停牌为 null，按中性色画，不当成 0 */
  pct5?: number | null
  pct20?: number | null
}

export interface HeatmapData {
  level: 'industry' | 'stock'
  industry: string | null
  items: HeatmapItem[]
  source: string
  updated_at: string
  coverage?: {
    classified?: number
    unclassified?: number
    unmapped_value_share?: number
  }
  /** 本地日线可用时才有 5 日/20 日两档 */
  periods_ready?: boolean
}

export const heatmapApi = {
  fetch: async (level: 'industry' | 'stock', industry?: string) => {
    const raw = await ApiClient.get<any>('/api/lite/heatmap', { level, industry: industry || '' }, { timeout: 30000 })
    return (raw as any)?.data as HeatmapData | null
  },
}

export interface ArenaBoardRow {
  persona: string
  style: string
  desc: string
  nav: number
  return_pct: number
  positions: number
  comment: string
  days: number
}

export interface ArenaNavPoint { date: string; nav: number; comment: string }

export interface ArenaPosition {
  symbol: string
  name: string
  shares: number
  avg_cost: number
  price: number
  pnl_pct: number
}

export interface ArenaTrade {
  date: string
  symbol: string
  side: string
  price: number
  shares: number
  reason: string
}


export interface PortfolioSignal { key: string; label: string; detail: string }

export interface PortfolioPosition {
  id: number
  symbol: string
  name: string
  shares: number
  buy_price: number
  cost: number
  buy_date: string
  source: string
  status: string
  price?: number
  market_value?: number
  pnl?: number
  pnl_pct?: number | null
  signals?: PortfolioSignal[]
  sell_date?: string
  sell_price?: number
  sell_reason?: string
}

export interface PortfolioSummary {
  open_count: number
  closed_count: number
  total_cost: number
  market_value: number
  unrealized_pnl: number
  unrealized_pnl_pct: number | null
  realized_pnl: number
  closed_win_rate: number | null
}

export interface PortfolioNavPoint {
  date: string
  market_value: number
  cost_value: number
  pnl_pct: number
  bench_cum_pct: number
}


export interface SmartSeatRow {
  seat: string
  count: number
  buy_yi: number
  sell_yi: number
  net_yi: number
  last_date: string
  stocks: string
}

export interface SeatWinrateRow {
  seat: string
  trades_5d: number
  avg_chg_5d: number
  win_rate_5d: number
  avg_chg_1d: number
  win_rate_1d: number
}

export interface FundHoldRow {
  symbol: string
  name: string
  funds: number
  mv_yi: number
  change: string
  change_pct: number
}
