import { ApiClient } from './request'

// 后端统一包装为 { success, data, message }。对标 marketSentimentApi 风格。
export const stockPickApi = {
  // 触发跑一次多 Agent 选股流水线（可传小 universe 做快速验证）
  run: (universe?: string[], maxCandidates = 40) =>
    ApiClient.post<any>('/api/quant/pipeline/run', { universe, max_candidates: maxCandidates }, { timeout: 300000 }),
  // 历史 run 列表
  runs: () => ApiClient.get<any>('/api/quant/pipeline/runs'),
  // 某次 run 详情
  detail: (runId: string) => ApiClient.get<any>(`/api/quant/pipeline/runs/${runId}`),
  // 触发 T+5 反向优汰复盘
  t5Review: (runId?: string) =>
    ApiClient.post<any>('/api/quant/pipeline/t5-review', null, { params: { run_id: runId }, timeout: 120000 }),
}
