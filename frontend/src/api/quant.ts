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

export const quantApi = {
  capabilities: async () =>
    unwrap<QuantCapabilitiesResult>(await ApiClient.get('/api/quant/capabilities', undefined, { timeout: 60000 })),

  smartPool: async (limit = 20, universeLimit = 300) => {
    const raw = unwrap<any>(await ApiClient.get('/api/lite/smart-pool', { limit, universe_limit: universeLimit, _ts: nonce() }, { timeout: 300000 }))
    const items = (raw.items || []).map((item: any) => ({
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
      strategy: raw.strategy,
      source: raw.source || 'lite-smart-pool',
      universe_size: raw.universe_size || items.length,
      analyzed: raw.analyzed || raw.universe_size || items.length,
      items,
      errors: raw.errors || {}
    } as QuantSmartPoolResult
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
