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
