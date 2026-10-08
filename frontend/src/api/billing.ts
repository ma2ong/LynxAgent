import { ApiClient, type ApiResponse } from './request'

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
  created_at: string
  last_login: string | null
  used_today: number
  used_total: number
}

export function adminListUsers() {
  return ApiClient.get<ApiResponse<AdminUser[]>>('/api/admin/users')
}

export function adminSetActive(username: string, isActive: boolean) {
  return ApiClient.put<ApiResponse<null>>(`/api/admin/users/${username}/active`, {
    is_active: isActive,
  })
}
