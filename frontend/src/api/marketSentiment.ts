import { ApiClient } from './request'

// Backend wraps as { success, data, message }.
export const marketSentimentApi = {
  get: (start?: string, end?: string) =>
    ApiClient.get<any>('/api/lite/market-sentiment', { start, end }, { timeout: 60000 }),
}
