import axios from 'axios'

const apiBaseURL = window.cometDesktop ? 'http://localhost:8000/api' : '/api'

// Browser builds use nginx/vite proxy. Desktop builds are loaded from file://,
// so they call the local Docker API directly.
const client = axios.create({
  baseURL: apiBaseURL,
  timeout: 300000,
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const projectId = sessionStorage.getItem('current_project_id')
  if (projectId) config.headers['X-Project-ID'] = projectId
  return config
})

client.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body.code !== 'undefined' && body.code !== 0) {
      return Promise.reject(new Error(body.message || '请求失败'))
    }
    return body
  },
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      if (window.cometDesktop) {
        window.location.hash = '#/login'
      } else if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    const message = error.response?.data?.message || error.message || '网络错误'
    return Promise.reject(new Error(message))
  },
)

export default client
