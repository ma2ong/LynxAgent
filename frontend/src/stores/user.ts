import { ref } from 'vue'
import { ApiClient, type ApiResponse } from '@/api/request'

export interface CurrentUser {
  id: string
  username: string
  email: string
  is_admin: boolean
  plan: string
  plan_expires_at: string | null
}

export const currentUser = ref<CurrentUser | null>(null)

export async function loadCurrentUser(force = false): Promise<CurrentUser | null> {
  if (currentUser.value && !force) return currentUser.value
  try {
    const res = await ApiClient.get<ApiResponse<CurrentUser>>('/api/auth/me')
    currentUser.value = (res?.data as CurrentUser) ?? null
  } catch {
    currentUser.value = null
  }
  return currentUser.value
}

export function clearCurrentUser() {
  currentUser.value = null
}
