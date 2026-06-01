import { ApiClient } from './request'

export interface PaperAccount {
  cash: { CNY: number }
  positions_value: { CNY: number }
  equity: { CNY: number }
  realized_pnl: { CNY: number }
  updated_at: string
}

export interface PaperPosition {
  code: string
  quantity: number
  avg_cost: number
  available_qty: number
  last_price: number | null
  market_value: number
  updated_at: string
}

export interface PaperOrder {
  id: string
  code: string
  side: string
  quantity: number
  price: number
  amount: number
  status: string
  created_at: string
  filled_at?: string
}

export interface PlaceOrderReq {
  code: string
  side: 'buy' | 'sell'
  quantity: number
}

// Backend wraps every response as { success, data, message }.
export const paperApi = {
  account: () =>
    ApiClient.get<any>('/api/paper/account'),
  orders: (limit = 50) =>
    ApiClient.get<any>('/api/paper/orders', { limit }),
  placeOrder: (payload: PlaceOrderReq) =>
    ApiClient.post<any>('/api/paper/order', payload),
  reset: () =>
    ApiClient.post<any>('/api/paper/reset', undefined, { params: { confirm: true } }),
}
