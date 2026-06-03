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
    wyckoff?: WyckoffAnalysis
    ml_features?: MlFeatureSnapshot
    multi_asset_hmm?: MultiAssetHmm
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

export interface WyckoffAnalysis {
  phase: string
  bias: string
  score: number
  accumulation_score?: number
  distribution_score?: number
  vol_spread_ratio?: number
  signals?: string[]
  reasons?: string[]
}

export interface MlFeatureSnapshot {
  feature_score: number
  trend_persistence: number
  risk_adjusted_momentum: number
  volatility_rank: number
  liquidity_quality: number
  drawdown_repair: number
}

export interface MultiAssetHmm {
  state: string
  peer_count?: number
  probabilities: Record<string, number>
  dimensions: {
    trend_regime: number
    volatility_regime: number
    cross_asset_correlation: number
    liquidity_regime: number
    mean_reversion_potential: number
  }
  mean_reversion: {
    score: number
    distance_to_lower_pct: number
    deviation_pct: number
    price: number
    bb_lower: number
    bb_mid: number
  }
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

export interface DataSourceStatusItem {
  key: string
  name: string
  installed: boolean
  enabled: boolean
  priority: number
  capabilities: string[]
  notes: string
}

export interface DataSourceStatusResult {
  sources: DataSourceStatusItem[]
  active_order: string[]
  primary: string
  fallback_enabled: boolean
}

const unwrap = <T>(response: any): T => {
  if (response && typeof response === 'object' && 'data' in response && 'success' in response) {
    return response.data as T
  }
  return response as T
}

export const quantApi = {
  capabilities: async () =>
    unwrap<QuantCapabilitiesResult>(await ApiClient.get('/api/quant/capabilities', undefined, { timeout: 60000 })),

  dataSources: async () =>
    unwrap<DataSourceStatusResult>(await ApiClient.get('/api/quant/data-sources', undefined, { timeout: 30000 })),

  quickCritic: async (symbols: string[], names?: Record<string, string>) =>
    unwrap<{ scores: Record<string, { score: number; keep: boolean; reject_reason: string | null }>; total: number }>(
      await ApiClient.post('/api/quant/pipeline/quick-critic', { symbols, names }, { timeout: 30000 })
    ),

  pipelineRun: (universe?: string[], maxCandidates = 40) =>
    ApiClient.post<any>('/api/quant/pipeline/run', { universe, max_candidates: maxCandidates }, { timeout: 300000 }),

  pipelineRuns: () => ApiClient.get<any>('/api/quant/pipeline/runs'),

  pipelineRunDetail: (runId: string) => ApiClient.get<any>(`/api/quant/pipeline/runs/${runId}`),

  pipelineT5Review: (runId?: string) =>
    ApiClient.post<any>('/api/quant/pipeline/t5-review', null, { params: { run_id: runId }, timeout: 120000 }),

  smartPool: async (limit = 20, universeLimit = 300) => {
    const raw = unwrap<any>(await ApiClient.get('/api/lite/smart-pool', { limit, universe_limit: universeLimit }, { timeout: 300000 }))
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
      exclude_fundamental: excludeFundamental
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
    unwrap<any>(await ApiClient.post('/api/lite/datalake/sync', {}, { params: { full }, timeout: 30000 })),

  syncStatus: async () =>
    unwrap<any>(await ApiClient.get('/api/lite/datalake/sync/status', undefined, { timeout: 15000 })),

  klineDetail: async (symbol: string, name = '', days = 250) =>
    unwrap<any>(await ApiClient.get('/api/quant/kline', { symbol, name, days }, { timeout: 60000 })),
}
