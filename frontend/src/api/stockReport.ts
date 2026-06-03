import { ApiClient } from './request'

// Backend (app/routers/quant.py /report) returns a raw dict or a { success, data } wrapper.
const unwrap = <T>(response: any): T => {
  if (response && typeof response === 'object' && 'data' in response && 'success' in response) {
    return response.data as T
  }
  return response as T
}

export const stockReportApi = {
  get: async (symbol: string) =>
    unwrap<any>(await ApiClient.get('/api/quant/report', { symbol }, { timeout: 120000 })),
}
