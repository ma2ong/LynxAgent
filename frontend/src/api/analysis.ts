import { ApiClient } from './request'

// Backend wraps responses as { success, data, message }.
export const analysisApi = {
  // Runs synchronously in SaaS Lite; returns { data: { task_id, status } }.
  runSingle: (symbol: string, market = 'A股') =>
    ApiClient.post<any>('/api/analysis/single', { symbol, parameters: { market_type: market } }),

  result: (taskId: string) =>
    ApiClient.get<any>(`/api/analysis/tasks/${taskId}/result`),
}
