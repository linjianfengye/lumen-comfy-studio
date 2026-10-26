import { useEffect, useState } from 'react'
import { useSession, useStatus, useStore } from './lib/store.js'
import { call, connect, login, logout, setup, SHOWCASE } from './lib/api.js'
import { providerById } from './lib/providers.js'
import { powerInfo, toast, Toaster } from './components/ui.jsx'
import Home from './pages/Home.jsx'
import Workflows from './pages/Workflows.jsx'
import Batch from './pages/Batch.jsx'
import Providers from './pages/Providers.jsx'
import Gallery from './pages/Gallery.jsx'
import Lab from './pages/Lab.jsx'
import Accounts from './pages/Accounts.jsx'

const ROUTES = [
  { path: '', label: '概览', el: Home },
  { path: 'workflows', label: '工作流', el: Workflows },
  { path: 'batch', label: '批量任务', el: Batch },
  { path: 'providers', label: '算力平台', el: Providers },
  { path: 'gallery', label: '作品库', el: Gallery },
  { path: 'lab', label: '色彩对比', el: Lab },
  { path: 'accounts', label: '账号', el: Accounts, admin: true },
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

// 第一次启动建管理员 / 登录
function AuthForm({ mode }) {
  const server = useSession().server
  const [f, setF] = useState({ username: '', password: '', code: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const isSetup = mode === 'setup'
  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      if (isSetup) await setup(f.username, f.password, f.code)
      else await login(f.username, f.password)
    } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  return (
    <div className="container gate">
      <form className="card form-grid" style={{ width: 'min(420px, 100%)' }} onSubmit={submit}>
        <h1 className="h2">{isSetup ? '创建管理员账号' : '登录 Lumen'}</h1>
        <p className="muted" style={{ margin: 0 }}>
          {isSetup ? '第一次使用：创建的账号是管理员，之后由管理员给其他人开账号。' : '账号由管理员分配。每个人的平台密钥、工作流和作品互相隔离。'}
        </p>
        <label className="field">
          <span>用户名</span>
          <input className="input mono" autoComplete="username" value={f.username} onChange={set('username')} />
        </label>
        <label className="field">
          <span>密码{isSetup ? '（至少 8 位）' : ''}</span>
          <input className="input" type="password" autoComplete={isSetup ? 'new-password' : 'current-password'} value={f.password} onChange={set('password')} />
        </label>
        {isSetup && server?.setupCode && (
          <label className="field">
            <span>初始化口令</span>
            <input className="input mono" type="password" autoComplete="off" value={f.code} onChange={set('code')} />
            <span className="hint">部署时设置的 LUMEN_TOKEN，防止别人抢先创建管理员</span>
          </label>
        )}
        {err && <p className="hint" role="alert" style={{ color: 'var(--red)' }}>{err}</p>}
        <button className="btn btn-primary" type="submit" disabled={busy || !f.username || !f.password}>{isSetup ? '创建并登录' : '登录'}</button>
        {server?.public && <p className="hint" style={{ margin: 0 }}>你填写的平台密钥会加密保存在服务器上，用于离线自动开关机；服务器运营者在技术上可以解密。</p>}
      </form>
    </div>
  )
}

// 后端未启动 / 需要登录时的占位页
function Gate({ status }) {
  if (status === 'auth' || status === 'setup') return <AuthForm mode={status === 'setup' ? 'setup' : 'login'} />
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

// 右上角账号：改密码、退出
function AccountMenu() {
  const user = useSession().user
  const [open, setOpen] = useState(false)
  if (!user) return null
  const changePassword = async () => {
    setOpen(false)
    const old = window.prompt('原密码')
    if (!old) return
    const pw = window.prompt('新密码（至少 8 位）')
    if (!pw) return
    if (await call('/me/password', { method: 'POST', body: { old, new: pw } })) toast('密码已修改，其他设备需要重新登录')
  }
  return (
    <div style={{ position: 'relative' }}>
      <button className="btn btn-ghost btn-sm" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        {user.username}{user.role === 'admin' ? ' · 管理员' : ''}
      </button>
      {open && (
        <div className="card" role="menu" style={{ position: 'absolute', right: 0, top: '110%', padding: 8, minWidth: 140, zIndex: 50, display: 'grid', gap: 4 }}>
          <button className="btn btn-ghost btn-sm" role="menuitem" onClick={changePassword}>修改密码</button>
          <button className="btn btn-ghost btn-sm" role="menuitem" onClick={() => { setOpen(false); logout() }}>退出登录</button>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const status = useStatus()
  const ready = useStore((s) => !!s)
  const user = useSession().user
  useEffect(() => { connect() }, [])
  const routes = ROUTES.filter((r) => !r.admin || user?.role === 'admin')
  const current = routes.find((r) => r.path === route) || routes[0]
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
            {routes.map((r) => (
              <a key={r.path} className="tab" href={`#/${r.path}`} aria-current={r === current ? 'page' : undefined}>{r.label}</a>
            ))}
          </div>
          {ready && <GpuPill />}
          {ready && status === 'offline' && <span className="chip red" role="status">后端已断开，重连中</span>}
          {SHOWCASE && <span className="chip blue" title="在线展示版：数据为预置快照，操作不会执行">展示版</span>}
          {ready && !SHOWCASE && <AccountMenu />}
        </nav>
      </header>
      <main key={current.path} className="page">
        {ready && status !== 'auth' && status !== 'setup' ? <Page /> : <Gate status={status} />}
      </main>
      <footer className="footer">
        <div className="container">Lumen · ComfyUI 多平台调度 · 计费为按单价估算，以平台账单为准 · 标有「演示」的平台不连接真实算力</div>
      </footer>
      <Toaster />
    </>
  )
}
