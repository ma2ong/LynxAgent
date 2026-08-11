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
  async (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (location.pathname !== '/login') location.href = '/login'
    }
    // 5xx/网络断连：多为后端重启窗口或上游数据源抖动。幂等 GET 静默重试一次，
    // 仍失败给可行动的中文提示，不把 "Request failed with status code 500" 裸抛给用户。
    const status = error?.response?.status
    const cfg = error?.config || {}
    const transient = (!error.response && error?.code !== 'ERR_CANCELED') || (status >= 500 && status < 600)
    if (transient && String(cfg.method || '').toLowerCase() === 'get' && !cfg._retried) {
      cfg._retried = true
      await new Promise((r) => setTimeout(r, 2000))
      try {
        return await http.request(cfg)
      } catch (e2: any) {
        error = e2?.config ? e2 : error
      }
    }
    if (transient) {
      return Promise.reject(new Error('服务暂时不可用（可能正在重启或数据源抖动），请稍后重试'))
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
    // detail 可能是字符串（FastAPI 默认）或 {code,message} 对象，两种都要能取到中文原因。
    const detail = error?.response?.data?.detail
    const reason = (typeof detail === 'string' ? detail : detail?.message)
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
