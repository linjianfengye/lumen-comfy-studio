// 页面共用的小组件与格式化函数
import { useEffect, useState } from 'react'
import { providerById } from '../lib/providers.js'

export const fmtClock = (sec) => {
  const s = Math.max(0, Math.ceil(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
export const fmtCny = (n) => `¥${n.toFixed(2)}`
export const fmtGb = (n) => (n ? `${n.toFixed(n < 10 ? 1 : 0)} GB` : '—')
export const fmtTime = (t) => new Date(t).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

// GPU 电源状态 → 圆点样式 + 文案
export function powerInfo(rt, providerId) {
  const p = providerById(providerId)
  if (p?.lifecycle === 'local') return rt.power === 'off' || rt.power === 'idle' ? { dot: 'idle', label: '本机待命 · 免费' } : { dot: 'on', label: '本机运算中' }
  switch (rt.power) {
    case 'starting': return { dot: 'wait', label: `冷启动中 ${Math.ceil(rt.startLeft)}s` }
    case 'running': return { dot: 'on', label: '运行中' }
    case 'idle': return { dot: 'idle', label: `空闲 ${fmtClock(rt.idleLeft)} 后关机` }
    case 'stopping': return { dot: 'wait', label: '正在关机' }
    default: return { dot: '', label: '已关机 · 不计费' }
  }
}

export function Switch({ checked, onChange, label }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-label={label} />
      <span />
    </label>
  )
}

export function Segmented({ value, options, onChange, label }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  )
}

export function Modal({ onClose, children, className = '', label }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${className}`} role="dialog" aria-modal="true" aria-label={label}>{children}</div>
    </div>
  )
}

// 作品媒体：真实 mp4/webm 用 <video>，动图 webp/gif 与图片用 <img>；演示视频用缓慢平移模拟
const VIDEO_SRC = /\.(mp4|webm|mov)(\?|$)|filename=[^&]*\.(mp4|webm|mov)/i
export const isVideoSrc = (src = '') => VIDEO_SRC.test(src)
export function Media({ item, className = '', style, alt = '', controls = false, mediaRef }) {
  if (isVideoSrc(item.src)) {
    return <video ref={mediaRef} className={className} style={style} src={item.src} autoPlay muted loop playsInline controls={controls} aria-label={alt || undefined} />
  }
  const pan = item.kind === 'video' && !item.real ? ' video-pan' : ''
  return <img ref={mediaRef} className={className + pan} style={style} src={item.src} alt={alt} loading="lazy" draggable="false" />
}

// 轻量全局提示
let pushToast = () => {}
export const toast = (msg) => pushToast(msg)
export function Toaster() {
  const [msg, setMsg] = useState(null)
  useEffect(() => {
    let t
    pushToast = (m) => { setMsg(m); clearTimeout(t); t = setTimeout(() => setMsg(null), 2600) }
    return () => clearTimeout(t)
  }, [])
  return msg ? <div className="toast" role="status">{msg}</div> : null
}

export const Icon = {
  upload: (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </svg>
  ),
  chevron: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>,
}
