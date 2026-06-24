import { ApiClient, type ApiResponse } from './request'

export interface WechatPushStatus {
  bound: boolean
  enabled: boolean
  serverchan_key_masked: string | null
  pushplus_token_masked: string | null
  updated_at: string | null
  member_push_allowed: boolean
}

export interface WechatBindPayload {
  serverchan_key?: string | null
  pushplus_token?: string | null
  enabled?: boolean
}

export function fetchWechatStatus() {
  return ApiClient.get<ApiResponse<WechatPushStatus>>('/api/notifications/wechat/status')
}

export function bindWechatPush(payload: WechatBindPayload) {
  return ApiClient.post<ApiResponse<WechatPushStatus>>('/api/notifications/wechat/bind', payload)
}

export function unbindWechatPush() {
  return ApiClient.delete<ApiResponse<WechatPushStatus>>('/api/notifications/wechat/bind')
}

export function testWechatPush() {
  return ApiClient.post<ApiResponse<any>>('/api/notifications/wechat/test')
}
