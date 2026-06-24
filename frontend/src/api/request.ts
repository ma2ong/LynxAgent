import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios'

/** Envelope returned by the backend: { success, data, message }. */
export interface ApiResponse<T = any> {
  success?: boolean
  data?: T
  message?: string
}

export type RequestConfig = AxiosRequestConfig

const TOKEN_KEY = 'auth-token'

const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 30000,
})

// Outgoing: stamp the saved JWT onto the Authorization header.
http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  return config
})

// Incoming: hand back the JSON body; bounce to login on an expired session.
let quotaDialogOpen = false

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (location.pathname !== '/login') location.href = '/login'
    }
    if (error?.response?.status === 402) {
      const detail = error.response.data?.detail
      const msg = detail?.message || '已达到套餐限制'
      if (!quotaDialogOpen) {
        quotaDialogOpen = true
        import('element-plus').then(({ ElMessageBox }) => {
          ElMessageBox.confirm(msg, detail?.code === 'member_required' ? '会员专属功能' : '今日额度已用完', {
            confirmButtonText: '了解会员',
            cancelButtonText: '我知道了',
            type: 'warning',
          }).then(() => {
            location.href = '/account/membership'
          }).catch(() => {}).finally(() => {
            quotaDialogOpen = false
          })
        })
      }
      return Promise.reject(new Error(msg))
    }
    const reason = error?.response?.data?.detail?.message
      || error?.response?.data?.message || error?.message || '请求失败'
    return Promise.reject(new Error(reason))
  },
)

/** Thin verb wrapper over the configured axios instance. */
export class ApiClient {
  static get<T = any>(url: string, params?: any, config?: RequestConfig): Promise<T> {
    return http.get(url, { params, ...config })
  }

  static post<T = any>(url: string, data?: any, config?: RequestConfig): Promise<T> {
    return http.post(url, data, config)
  }

  static put<T = any>(url: string, data?: any, config?: RequestConfig): Promise<T> {
    return http.put(url, data, config)
  }

  static delete<T = any>(url: string, config?: RequestConfig): Promise<T> {
    return http.delete(url, config)
  }

  static patch<T = any>(url: string, data?: any, config?: RequestConfig): Promise<T> {
    return http.patch(url, data, config)
  }
}

export default http
export { http as request }
