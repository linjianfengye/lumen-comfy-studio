// 页面调用的操作：全部转发到后端，状态变化由 SSE 推回来。
// 这里只保留纯计算的辅助函数（价格、就绪判断），不再在前端模拟调度。
import { call } from './api.js'
import { refreshLocalBridge } from './localBridge.js'
import { providerById } from './providers.js'
import { getState } from './store.js'
import { toast } from '../components/ui.jsx'

export const USD_TO_CNY = 7.2

export const priceOf = (providerId, gpuId) =>
  providerById(providerId)?.gpus.find((g) => g.id === gpuId)?.price ?? 0

// ---------- 查询（与后端 engine.ready_on 规则一致） ----------
export const comfyReadyIds = (s) => Object.keys(s.providers).filter((id) => s.providers[id].connected && s.providers[id].comfy.status === 'ready')

export function workflowReadyOn(s, wf, pid) {
  const mode = s.providers[pid]?.modelMode
  if (mode === 'manual') return true
  if (mode === 'check') return !!wf.checks?.[pid] && wf.checks[pid].status !== 'checking'
  return wf.models.every((m) => (s.modelCache[pid] || []).includes(m.name))
}

// 某个模型在某个平台上的状态：cached / downloading / queued / pending / missing / checking / manual / error
export function modelState(s, wf, m, pid) {
  const mode = s.providers[pid]?.modelMode
  if (mode === 'manual') return { status: 'manual', progress: 0 }
  if (mode === 'check') {
    const c = wf.checks?.[pid]
    if (!c || c.status === 'pending') return { status: 'pending', progress: 0 }
    if (c.status === 'checking') return { status: 'checking', progress: 0 }
    if (c.status === 'error') return { status: 'error', progress: 0 }
    return c.missingModels.includes(m.name) ? { status: 'missing', progress: 0 } : { status: 'cached', progress: 1 }
  }
  if ((s.modelCache[pid] || []).includes(m.name)) return { status: 'cached', progress: 1 }
  const t = wf.targets?.[pid]
  if (t?.status === 'deploying') return t.current === m.name ? { status: 'downloading', progress: t.progress } : { status: 'queued', progress: 0 }
  if (t?.status === 'error' && !m.source) return { status: 'error', progress: 0 }
  return { status: 'pending', progress: 0 }
}

// ---------- 平台 ----------
export const connectProvider = (id, config, { demo = false, deploy = false } = {}) =>
  call(`/providers/${id}/connect`, { method: 'POST', body: { config, demo, deploy } })
export const testProvider = (id, config, demo) => call(`/providers/${id}/test`, { method: 'POST', body: { config, demo } })
export const disconnectProvider = (id) => call(`/providers/${id}/disconnect`, { method: 'POST' })
export const setGpu = (id, gpu) => call(`/providers/${id}`, { method: 'PATCH', body: { gpu } })
export const setActiveProvider = (id) => call(`/providers/${id}`, { method: 'PATCH', body: { active: true } })
export const setPolicy = (patch) => call('/policy', { method: 'PATCH', body: patch })
export const startProvider = (id) => call(`/providers/${id}/start`, { method: 'POST' })
export const stopProvider = (id) => call(`/providers/${id}/stop`, { method: 'POST' })
export const deployComfy = (ids) => call('/providers/deploy', { method: 'POST', body: { ids } })

// ---------- 工作流 ----------
export function importWorkflow(file) {
  const form = new FormData()
  form.append('file', file, file.name)
  return call('/workflows', { method: 'POST', form })
}
export const importSample = (json, name) => importWorkflow(new File([JSON.stringify(json)], name, { type: 'application/json' }))
export async function deployWorkflow(wfId, ids) {
  // 浏览器直连的本机 ComfyUI：先重新读取节点列表，否则看不到刚放进去的模型
  const local = getState()?.providers?.local
  if (ids.includes('local') && local?.comfy?.bridge) {
    try { await refreshLocalBridge() } catch (e) { toast(e.message); return null }
  }
  return call(`/workflows/${wfId}/deploy`, { method: 'POST', body: { ids } })
}
export const removeWorkflow = (wfId) => call(`/workflows/${wfId}`, { method: 'DELETE' })
export const setModelSource = (wfId, name, source) => call(`/workflows/${wfId}/models`, { method: 'PATCH', body: { name, source } })

// ---------- 批量任务 ----------
export async function createBatch({ firstFrame, ...body }) {
  if (firstFrame) {
    const form = new FormData()
    form.append('file', firstFrame, firstFrame.name)
    const up = await call('/uploads', { method: 'POST', form })
    if (!up) return null
    body.firstFrameId = up.id
  }
  return call('/batches', { method: 'POST', body })
}
export const cancelBatch = (id) => call(`/batches/${id}/cancel`, { method: 'POST' })
export const clearFinishedBatches = () => call('/batches/clear', { method: 'POST' })

// ---------- 作品 ----------
export const removeGalleryItem = (id) => call(`/gallery/${id}`, { method: 'DELETE' })
export const setCompare = (slot, id) => call('/compare', { method: 'PUT', body: { [slot]: id } })
