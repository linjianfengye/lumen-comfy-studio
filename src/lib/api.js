// 与 Lumen 后端通信：REST 操作 + SSE 状态推送。状态以后端为准，前端只渲染。
import { setState, setStatus } from './store.js'
import { toast } from '../components/ui.jsx'

export class ApiError extends Error {}

// 静态展示版（npm run build:showcase）：没有后端，读一份预置的状态快照，操作只提示
export const SHOWCASE = import.meta.env.VITE_SHOWCASE === '1'
const SHOWCASE_MSG = '这是在线展示版，连接平台、出图等操作需要在本机运行完整版（npm run server + npm run dev）'

export async function api(path, { method = 'GET', body, form } = {}) {
  if (SHOWCASE) throw new ApiError(SHOWCASE_MSG)
  let res
  try {
    res = await fetch('/api' + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: form || (body !== undefined ? JSON.stringify(body) : undefined),
      credentials: 'same-origin',
    })
  } catch {
    setStatus('offline')
    throw new ApiError('连不上 Lumen 后端，请确认已启动')
  }
  if (res.status === 401 && path !== '/login') {
    setStatus('auth')
    throw new ApiError('需要登录')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `请求失败（${res.status}）`)
  return data
}

// 调用后端操作：失败时弹提示，返回 null
export async function call(path, opts) {
  try {
    return await api(path, opts)
  } catch (e) {
    if (e.message !== '需要登录') toast(e.message)
    return null
  }
}

let source = null
let retry = null

export async function connect() {
  if (SHOWCASE) {
    const res = await fetch('/showcase-state.json')
    setState(await res.json())
    setStatus('ready')
    return
  }
  clearTimeout(retry)
  source?.close()
  try {
    setState(await api('/state'))
  } catch (e) {
    if (e.message !== '需要登录') retry = setTimeout(connect, 3000)
    return
  }
  setStatus('ready')
  source = new EventSource('/api/events')
  source.addEventListener('state', (ev) => { setState(JSON.parse(ev.data)); setStatus('ready') })
  source.addEventListener('notice', (ev) => toast(JSON.parse(ev.data)))
  // EventSource 会自动重连；连续失败时重新走一遍（可能是后端重启或登录过期）
  source.onerror = () => {
    setStatus('offline')
    source.close()
    retry = setTimeout(connect, 3000)
  }
}

export async function login(token) {
  await api('/login', { method: 'POST', body: { token } })
  await connect()
}
