// 浏览器直连本机 ComfyUI：服务器不访问你电脑上的 127.0.0.1，由这个网页去访问。
// 连接时读取 /system_stats 和 /object_info 交给后端；之后轮询后端领取任务，提交给本机 ComfyUI，再把结果传回。
// 需要 ComfyUI 以 --enable-cors-header 启动，并保持本网页打开。
import { api } from './api.js'
import { getState } from './store.js'

const POLL_MS = 2000
let timer = null
const running = new Map() // jobId -> { promptId, endpoint }

const isBridge = () => !!getState()?.providers?.local?.comfy?.bridge
const endpointOf = () => (getState()?.providers?.local?.config?.endpoint || 'http://127.0.0.1:8188').replace(/\/+$/, '')

async function comfy(endpoint, path, opts) {
  let res
  try {
    res = await fetch(endpoint + path, opts)
  } catch {
    throw new Error(`浏览器连不上 ${endpoint}：请确认 ComfyUI 已启动，并带上 --enable-cors-header 参数`)
  }
  if (!res.ok) {
    let msg = `ComfyUI 返回 ${res.status}`
    try { const j = await res.json(); msg = j?.error?.message || j?.message || msg } catch { /* 非 JSON */ }
    throw new Error(msg)
  }
  return res
}

// 连接窗口里调用：浏览器直接测试并把信息交给后端
export async function connectLocalBridge(endpoint) {
  const ep = endpoint.trim().replace(/\/+$/, '')
  const stats = await (await comfy(ep, '/system_stats')).json()
  const objectInfo = await (await comfy(ep, '/object_info')).json()
  const r = await api('/bridge/connect', { method: 'POST', body: { endpoint: ep, stats, objectInfo } })
  startBridge()
  return r.info
}

// 重新读取本机 ComfyUI 的节点列表（核对模型前调用）
export const refreshLocalBridge = () => connectLocalBridge(endpointOf())

// 只做测试，不保存
export async function testLocalBridge(endpoint) {
  const ep = endpoint.trim().replace(/\/+$/, '')
  const stats = await (await comfy(ep, '/system_stats')).json()
  const d = stats.devices?.[0] || {}
  const vram = d.vram_total ? ` · ${(d.vram_total / 2 ** 30).toFixed(1)} GB` : ''
  return `ComfyUI ${stats.system?.comfyui_version || ''} · ${d.name || '本机 GPU'}${vram}`
}

function pickOutput(entry) {
  const files = []
  for (const out of Object.values(entry?.outputs || {})) {
    for (const key of ['videos', 'gifs', 'images']) {
      for (const f of out[key] || []) {
        if (f.type === 'temp') continue
        files.push({ ...f, video: key !== 'images' || /\.(mp4|webm|mov)$/i.test(f.filename) })
      }
    }
  }
  return files.find((f) => f.video) || files[0]
}
async function runJob(job) {
  const ep = endpointOf()
  const report = (path, body) => api(`/bridge/jobs/${job.id}/${path}`, { method: 'POST', body }).catch(() => null)
  try {
    if (job.upload) {
      const blob = await (await fetch(`/api/bridge/uploads/${encodeURIComponent(job.upload.file)}`, { credentials: 'same-origin' })).blob()
      const fd = new FormData()
      fd.append('image', blob, job.upload.name)
      fd.append('overwrite', 'true')
      await comfy(ep, '/upload/image', { method: 'POST', body: fd })
    }
    const clientId = crypto.randomUUID()
    const sub = await (await comfy(ep, '/prompt', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: job.prompt, client_id: clientId }),
    })).json()
    const promptId = sub.prompt_id
    running.set(job.id, { promptId, endpoint: ep })
    const t0 = Date.now()
    let entry = null
    while (running.has(job.id)) {
      await new Promise((r) => setTimeout(r, 1500))
      const h = await (await comfy(ep, `/history/${promptId}`)).json()
      entry = h[promptId]
      if (entry?.status?.status_str === 'error') throw new Error('ComfyUI 执行出错，请在 ComfyUI 界面查看')
      if (entry?.outputs && Object.keys(entry.outputs).length && entry.status?.completed !== false) break
      // 没有逐步进度（不用 WebSocket），按时间给个估计值
      report('progress', { value: Math.min(0.9, (Date.now() - t0) / 60000) })
    }
    if (!running.has(job.id)) return   // 已取消
    const f = pickOutput(entry)
    if (!f) throw new Error('工作流没有输出文件（缺少 SaveImage 等保存节点）')
    const q = new URLSearchParams({ filename: f.filename, subfolder: f.subfolder || '', type: f.type || 'output' })
    const blob = await (await comfy(ep, `/view?${q}`)).blob()
    const fd = new FormData()
    fd.append('file', blob, f.filename)
    fd.append('video', f.video ? '1' : '0')
    await api(`/bridge/jobs/${job.id}/result`, { method: 'POST', form: fd })
  } catch (e) {
    await report('fail', { error: e.message })   // 已取消的任务后端会忽略
  } finally {
    running.delete(job.id)
  }
}

async function tick() {
  if (!isBridge()) return
  let r
  try {
    r = await api('/bridge/poll', { method: 'POST' })
  } catch {
    return
  }
  for (const id of r.cancel || []) {
    const cur = running.get(id)
    if (cur) {
      running.delete(id)
      fetch(cur.endpoint + '/interrupt', { method: 'POST' }).catch(() => {})
    }
  }
  for (const job of r.jobs || []) runJob(job)
}

export function startBridge() {
  if (timer) return
  timer = setInterval(tick, POLL_MS)
  tick()
}

export function stopBridge() {
  clearInterval(timer)
  timer = null
}
