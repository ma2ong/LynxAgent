import { ApiClient, type ApiResponse } from './request'

export interface BillingMe {
  plan: string
  plan_label: string
  plan_expires_at: string | null
  daily_limit: number
  used_today: number
  remaining_today: number
  features: string[]
}

export function fetchBillingMe() {
  return ApiClient.get<ApiResponse<BillingMe>>('/api/billing/me')
}

export interface UpgradeInfo {
  price_text: string
  alipay_id: string
  qr_url: string
  configured: boolean
  instructions: string
}

export function fetchUpgradeInfo() {
  return ApiClient.get<ApiResponse<UpgradeInfo>>('/api/billing/upgrade-info')
}

export interface RuntimeConfigCheck {
  key: string
  label: string
  ok: boolean
  required: boolean
  message: string
}

export interface RuntimeConfigValidation {
  valid: boolean
  mode: string
  storage: string
  checks: RuntimeConfigCheck[]
  warnings: string[]
}

export function fetchRuntimeValidation() {
  return ApiClient.get<ApiResponse<RuntimeConfigValidation>>('/api/system/config/validate')
}

export interface AdminUser {
  id: string
  username: string
  email: string
  is_admin: number
  is_active: number
  plan: string
  plan_expires_at: string | null
  created_at: string
  last_login: string | null
  used_today: number
  used_total: number
}

export function adminListUsers() {
  return ApiClient.get<ApiResponse<AdminUser[]>>('/api/admin/users')
}

export function adminSetPlan(username: string, plan: string, expiresAt: string | null) {
  return ApiClient.put<ApiResponse<null>>(`/api/admin/users/${username}/plan`, {
    plan,
    plan_expires_at: expiresAt,
  })
}

export function adminSetActive(username: string, isActive: boolean) {
  return ApiClient.put<ApiResponse<null>>(`/api/admin/users/${username}/active`, {
    is_active: isActive,
  })
}
