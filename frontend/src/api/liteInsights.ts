import { ApiClient } from './request'

export interface CatalystItem {
  symbol: string
  name: string
  industry?: string
  board?: string
  sector?: string
  score: number
  signal: string
  mentions: number
  hot_score: number
  sentiment: number
  change_percent: number
  price: number
  risk_level: string
  sparkline: number[]
  reasons: string[]
  updated_at: string
}

export const liteInsightsApi = {
  getCatalysts(params: { window?: string; threshold?: number; limit?: number } = {}) {
    return ApiClient.get('/api/lite/catalysts', {
      window: params.window || '24h',
      threshold: params.threshold ?? 1.5,
      limit: params.limit ?? 10
    })
  }
}
