// 平台目录：与后端共用 shared/providers.json。
//   lifecycle：serverless 平台自行缩容 / instance 由 Lumen 关机 / local 本机
//   adapter：后端已实现真实接入；否则只能用演示模式
//   ui：部署后「打开 ComfyUI 界面」的方式（endpoint 用接口地址，field 另填地址，null 只有 API）
// 价格为演示估算（USD/小时），免费额度以各平台官网为准。
import PROVIDERS from '../../shared/providers.json'

export { PROVIDERS }
export const providerById = (id) => PROVIDERS.find((p) => p.id === id)

export const LIFECYCLE_LABEL = {
  serverless: '平台自动缩容',
  instance: 'Lumen 定时关机',
  local: '本机',
}

export function comfyUiLink(p, state) {
  if (!p?.ui || !state) return null
  const cfg = state.config || {}
  const url = p.ui === 'field' ? cfg.uiUrl : state.comfy?.url || cfg.endpoint || (p.id === 'local' ? 'http://127.0.0.1:8188' : '')
  return /^https?:\/\/[^<>\s]+$/.test((url || '').trim()) ? url.trim().replace(/\/+$/, '') : null
}
