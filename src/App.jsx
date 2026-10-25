import { useEffect, useState } from 'react'
import { useStatus, useStore } from './lib/store.js'
import { connect, login } from './lib/api.js'
import { providerById } from './lib/providers.js'
import { powerInfo, Toaster } from './components/ui.jsx'
import Home from './pages/Home.jsx'
import Workflows from './pages/Workflows.jsx'
import Batch from './pages/Batch.jsx'
import Providers from './pages/Providers.jsx'
import Gallery from './pages/Gallery.jsx'
import Lab from './pages/Lab.jsx'

const ROUTES = [
  { path: '', label: '概览', el: Home },
  { path: 'workflows', label: '工作流', el: Workflows },
  { path: 'batch', label: '批量任务', el: Batch },
  { path: 'providers', label: '算力平台', el: Providers },
  { path: 'gallery', label: '作品库', el: Gallery },
  { path: 'lab', label: '色彩对比', el: Lab },
]

const readHash = () => window.location.hash.replace(/^#\/?/, '')

function useRoute() {
  const [route, setRoute] = useState(readHash)
  useEffect(() => {
    const on = () => { setRoute(readHash()); window.scrollTo({ top: 0 }) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

// 多平台同时在线时显示汇总，否则显示默认平台
function GpuPill() {
  const active = useStore((s) => s.activeProvider)
  const runtime = useStore((s) => s.runtime)
  const gpu = useStore((s) => s.providers[s.activeProvider].gpu)
  const online = Object.keys(runtime).filter((id) => runtime[id].power !== 'off' && providerById(id).lifecycle !== 'local')
  const info = powerInfo(runtime[active], active)
  if (online.length > 1) {
    return (
      <a className="status-pill" href="#/" aria-label={`${online.length} 个平台在线`}>
        <span className="dot on" />
        <span>{online.length} 个平台在线</span>
        <span className="muted">{online.map((id) => providerById(id).name).join(' · ')}</span>
      </a>
    )
  }
  return (
    <a className="status-pill" href="#/providers" aria-label={`当前算力：${providerById(active).name} ${gpu}，${info.label}`}>
      <span className={`dot ${info.dot}`} />
      <span>{providerById(active).name} · {gpu}</span>
      <span className="muted tabular">{info.label}</span>
    </a>
  )
}

// 后端未启动 / 需要登录时的占位页
function Gate({ status }) {
  const [token, setToken] = useState('')
  const [err, setErr] = useState('')
  if (status === 'auth') {
    return (
      <div className="container gate">
        <form className="card form-grid" style={{ width: 'min(420px, 100%)' }}
          onSubmit={async (e) => { e.preventDefault(); try { await login(token) } catch (x) { setErr(x.message) } }}>
          <h1 className="h2">登录 Lumen</h1>
          <p className="muted" style={{ margin: 0 }}>后端设置了 LUMEN_TOKEN，请输入令牌。</p>
          <label className="field">
            <span>令牌</span>
            <input className="input mono" type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} />
          </label>
          {err && <p className="hint" role="alert" style={{ color: 'var(--red)' }}>{err}</p>}
          <button className="btn btn-primary" type="submit" disabled={!token}>登录</button>
        </form>
      </div>
    )
  }
  return (
    <div className="container gate">
      <div className="card" style={{ width: 'min(560px, 100%)' }} role="status">
        <p className="eyebrow">{status === 'offline' ? '连不上后端' : '正在连接后端'}</p>
        <h1 className="h2">{status === 'offline' ? 'Lumen 后端没有运行' : '稍等一下…'}</h1>
        {status === 'offline' && (
          <>
            <p className="muted">在 comfy-studio/server 目录启动后端，页面会自动重连：</p>
            <pre className="code">.venv\Scripts\python -m lumen</pre>
          </>
        )}
      </div>
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const status = useStatus()
  const ready = useStore((s) => !!s)
  useEffect(() => { connect() }, [])
  const current = ROUTES.find((r) => r.path === route) || ROUTES[0]
  const Page = current.el

  return (
    <>
      <header className="nav-wrap">
        <nav className="nav" aria-label="主导航">
          <a className="brand" href="#/">
            <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
              <rect width="32" height="32" rx="9" fill="#1d1d1f" />
              <circle cx="16" cy="16" r="8" fill="none" stroke="#fff" strokeWidth="2.4" />
              <circle cx="21.5" cy="10.5" r="3" fill="#1a73e8" />
            </svg>
            Lumen
          </a>
          <div className="tabs">
            {ROUTES.map((r) => (
              <a key={r.path} className="tab" href={`#/${r.path}`} aria-current={r === current ? 'page' : undefined}>{r.label}</a>
            ))}
          </div>
          {ready && <GpuPill />}
          {ready && status === 'offline' && <span className="chip red" role="status">后端已断开，重连中</span>}
        </nav>
      </header>
      <main key={current.path} className="page">
        {ready && status !== 'auth' ? <Page /> : <Gate status={status} />}
      </main>
      <footer className="footer">
        <div className="container">Lumen · ComfyUI 多平台调度 · 计费为按单价估算，以平台账单为准 · 标有「演示」的平台不连接真实算力</div>
      </footer>
      <Toaster />
    </>
  )
}
