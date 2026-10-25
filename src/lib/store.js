// 前端状态：后端快照的只读副本（由 api.js 的 SSE 推送更新）。页面通过 useStore 订阅。
import { useSyncExternalStore } from 'react'

let state = null
let status = 'connecting' // connecting | ready | offline | auth
const listeners = new Set()
const emit = () => listeners.forEach((l) => l())
const subscribe = (l) => (listeners.add(l), () => listeners.delete(l))

export function setState(next) {
  state = next
  emit()
}

export function setStatus(next) {
  if (status !== next) {
    status = next
    emit()
  }
}

export const getState = () => state

// App 在 state 就绪前不渲染页面，所以页面里的 selector 可以假定 state 存在
export function useStore(selector = (s) => s) {
  return useSyncExternalStore(subscribe, () => (state ? selector(state) : undefined))
}

export const useStatus = () => useSyncExternalStore(subscribe, () => status)
