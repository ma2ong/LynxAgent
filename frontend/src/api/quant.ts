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
}

export interface QuantSmartPoolResult {
  strategy?: string
  source: string
  universe_size: number
  analyzed?: number
  ai_factor?: {
    status?: string
    pick_date?: string
    universe?: number
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
    items,
    errors: raw?.errors || {}
  }
}

export const quantApi = {
  capabilities: async () =>
    unwrap<QuantCapabilitiesResult>(await ApiClient.get('/api/quant/capabilities', undefined, { timeout: 60000 })),

  smartPool: async (limit = 20, universeLimit = 300) => {
    const raw = unwrap<any>(await ApiClient.get('/api/lite/smart-pool', { limit, universe_limit: universeLimit, _ts: nonce() }, { timeout: 300000 }))
    return normalizeSmartPoolResult(raw)
  },

  startSmartPoolTask: async (limit = 20, universeLimit = 300, strategy = 'balanced') =>
    unwrap<QuantSmartPoolTask>(await ApiClient.post('/api/lite/smart-pool/tasks', undefined, {
      params: { limit, universe_limit: universeLimit, strategy, _ts: nonce() },
      timeout: 15000
    })),

  smartPoolTask: async (taskId: string) => {
    const task = unwrap<any>(await ApiClient.get(`/api/lite/smart-pool/tasks/${taskId}`, { _ts: nonce() }, { timeout: 15000 }))
    if (task?.result) {
      task.result = normalizeSmartPoolResult(unwrap<any>(task.result))
    }
    return task as QuantSmartPoolTask
  },

  patternPool: async (limit = 20, universeLimit = 500, minStrength = 70, excludeFundamental = true) => {
    const raw = unwrap<any>(await ApiClient.get('/api/quant/pattern-pool', {
      limit,
      universe_limit: universeLimit,
      min_strength: minStrength,
      exclude_fundamental: excludeFundamental,
      _ts: nonce()
    }, { timeout: 300000 }))
    const items = (raw.items || []).map((item: any) => ({
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
      matched_patterns: item.matched_patterns || item.patterns || []
    }))
    return {
      source: raw.source || 'pre-lift-pattern-pool',
      universe_size: raw.universe_size || items.length,
      analyzed: raw.analyzed || raw.universe_size || items.length,
      matched: raw.matched || items.length,
      items,
      errors: raw.errors || {},
      pattern_model: raw.pattern_model || [],
      excluded: raw.excluded,
      excluded_reasons: raw.excluded_reasons,
      scanned: raw.scanned
    } as QuantPatternPoolResult
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

  // ---- 资金面：资金流向 / 龙虎榜 / 财经日历 ----
  capitalIndustryFlow: async () =>
    unwrap<any>(await ApiClient.get('/api/quant/capital/industry-flow', undefined, { timeout: 60000 })),
  capitalConceptFlow: async () =>
    unwrap<any>(await ApiClient.get('/api/quant/capital/concept-flow', undefined, { timeout: 60000 })),
  capitalStockFlow: async (limit = 50) =>
    unwrap<any>(await ApiClient.get('/api/quant/capital/stock-flow', { limit }, { timeout: 60000 })),
  dragonTiger: async (date = '') =>
    unwrap<any>(await ApiClient.get('/api/quant/dragon-tiger', { date }, { timeout: 60000 })),
  dragonTigerSeats: async (symbol: string, date = '') =>
    unwrap<any>(await ApiClient.get('/api/quant/dragon-tiger/seats', { symbol, date }, { timeout: 60000 })),
  capitalCalendar: async (types = 'earnings,unlock,ipo', days = 14) =>
    unwrap<any>(await ApiClient.get('/api/quant/calendar', { types, days }, { timeout: 60000 })),

  // ---- 加权情绪：大盘 / 板块 ----
  marketWeightedSentiment: async () =>
    unwrap<any>(await ApiClient.get('/api/quant/market/weighted-sentiment', undefined, { timeout: 60000 })),
  sectorSentimentRank: async (limit = 20) =>
    unwrap<any>(await ApiClient.get('/api/quant/sector/sentiment-rank', { limit }, { timeout: 60000 })),

  // ---- 个股深研增强：加权情绪 / 投资者画像 / 红旗（后两者走 LLM）----
  stockSentiment: async (symbol: string) =>
    unwrap<any>(await ApiClient.get('/api/quant/stock/sentiment', { symbol }, { timeout: 60000 })),
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

  picksStats: async (days = 30, pool = '') =>
    unwrap<PicksStatsResult>(await ApiClient.get('/api/quant/picks/stats', { days, pool, _ts: nonce() }, { timeout: 120000 })),

  marketContext: async () =>
    unwrap<MarketContext>(await ApiClient.get('/api/quant/market-context', { _ts: nonce() }, { timeout: 30000 })),

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
  median_5d_pct?: number
  breadth_up?: number
  as_of?: string
  advice?: string
}

export interface PicksHorizonStat {
  samples: number
  win_rate: number | null
  avg_return: number | null
  excess_win_rate?: number | null
  avg_excess?: number | null
}

export interface PicksPoolStat {
  pool: string
  picks: number
  horizons: { t1: PicksHorizonStat; t3: PicksHorizonStat; t5: PicksHorizonStat }
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

export interface PicksStatsResult {
  days: number
  since: string
  total_picks: number
  pools: PicksPoolStat[]
  items: PicksStatsItem[]
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

export const reportsApi = {
  latest: async (kind: 'premarket' | 'close') => {
    const raw = await ApiClient.get<any>('/api/lite/reports/latest', { kind })
    return (raw as any)?.data as DailyReport | null
  },
  byDate: async (date: string, kind: 'premarket' | 'close') => {
    const raw = await ApiClient.get<any>('/api/lite/reports', { date, kind })
    return (raw as any)?.data as DailyReport | null
  },
  available: async () => {
    const raw = await ApiClient.get<any>('/api/lite/reports')
    return ((raw as any)?.data?.available ?? []) as { date: string; kind: string }[]
  },
  generate: async (kind: 'premarket' | 'close') => {
    const raw = await ApiClient.post<any>(`/api/lite/reports/generate?kind=${kind}`, undefined, { timeout: 180000 })
    return (raw as any)?.data as DailyReport | null
  },
}

export interface PanelVerdict {
  persona: string
  style: string
  score: number
  stance: string
  reason: string
}

export interface PanelScore {
  consensus_score: number
  divergence: number
  bull_count: number
  bear_count: number
  verdicts: PanelVerdict[]
  summary: string
}

export interface PanelBatchData {
  date: string
  pool: string
  items: Record<string, PanelScore>
  pending: number
  llm: boolean
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
}

export interface HeatmapData {
  level: 'industry' | 'stock'
  industry: string | null
  items: HeatmapItem[]
  source: string
  updated_at: string
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

export const arenaApi = {
  board: async () => {
    const raw = await ApiClient.get<any>('/api/lite/arena')
    return (raw as any)?.data as { board: ArenaBoardRow[]; series: Record<string, ArenaNavPoint[]> } | null
  },
  detail: async (persona: string) => {
    const raw = await ApiClient.get<any>('/api/lite/arena/detail', { persona })
    return (raw as any)?.data as { persona: string; cash: number; positions: ArenaPosition[]; trades: ArenaTrade[] } | null
  },
  run: async () => {
    const raw = await ApiClient.post<any>('/api/lite/arena/run', undefined, { timeout: 180000 })
    return (raw as any)?.data
  },
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

export const portfolioApi = {
  list: async () => {
    const raw = await ApiClient.get<any>('/api/lite/portfolio', { _ts: nonce() })
    return (raw as any)?.data as { open: PortfolioPosition[]; closed: PortfolioPosition[]; summary: PortfolioSummary } | null
  },
  add: async (payload: { symbol: string; name?: string; source?: string; budget?: number }) => {
    const raw = await ApiClient.post<any>('/api/lite/portfolio/add', payload, { timeout: 30000 })
    return (raw as any)?.data
  },
  addBatch: async (payload: {
    items: { symbol: string; name?: string; price?: number }[]
    budget_per_stock?: number
    source?: string
  }) => {
    const raw = await ApiClient.post<any>('/api/lite/portfolio/add-batch', payload, { timeout: 60000 })
    return (raw as any)?.data as { added: number; skipped: number; results: { symbol: string; name: string; ok: boolean; reason?: string }[] } | null
  },
  sell: async (id: number, reason = 'manual') => {
    const raw = await ApiClient.post<any>('/api/lite/portfolio/sell', { id, reason }, { timeout: 30000 })
    return (raw as any)?.data
  },
  nav: async () => {
    const raw = await ApiClient.get<any>('/api/lite/portfolio/nav', { _ts: nonce() })
    return (raw as any)?.data as PortfolioNavPoint[] | null
  },
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

export const smartMoneyApi = {
  seats: async (days = 30) => {
    const raw = await ApiClient.get<any>('/api/quant/smart-money/seats', { days }, { timeout: 60000 })
    return raw as { empty: boolean; message?: string; rows: SmartSeatRow[] }
  },
  winrate: async () => {
    const raw = await ApiClient.get<any>('/api/quant/smart-money/seat-winrate', undefined, { timeout: 60000 })
    return raw as { empty: boolean; message?: string; rows: SeatWinrateRow[] }
  },
  fund: async () => {
    const raw = await ApiClient.get<any>('/api/quant/smart-money/fund-consensus', undefined, { timeout: 60000 })
    return raw as { empty: boolean; message?: string; quarter?: string; rows: FundHoldRow[] }
  },
}
