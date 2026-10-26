// 管理员：建账号、停用 / 启用、重置密码。每个账号的平台密钥、工作流、作品互相隔离。
import { useEffect, useState } from 'react'
import { call } from '../lib/api.js'
import { useSession } from '../lib/store.js'
import { toast } from '../components/ui.jsx'

export default function Accounts() {
  const me = useSession().user
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', role: 'user' })

  const load = async () => {
    const r = await call('/admin/users')
    if (r) setUsers(r.users)
  }
  useEffect(() => { load() }, [])

  if (me?.role !== 'admin') {
    return <div className="container"><div className="card"><p className="muted">只有管理员可以管理账号。</p></div></div>
  }

  const create = async (e) => {
    e.preventDefault()
    const r = await call('/admin/users', { method: 'POST', body: form })
    if (!r) return
    toast(`已创建账号 ${r.user.username}，请把用户名和密码发给对方`)
    setForm({ username: '', password: '', role: 'user' })
    load()
  }
  const toggle = async (u) => {
    if (await call(`/admin/users/${u.id}`, { method: 'PATCH', body: { disabled: !u.disabled } })) {
      toast(u.disabled ? `已启用 ${u.username}` : `已停用 ${u.username}，排队任务已取消，开着的实例已关机`)
      load()
    }
  }
  const reset = async (u) => {
    const pw = window.prompt(`给 ${u.username} 设置新密码（至少 8 位）`)
    if (!pw) return
    if (await call(`/admin/users/${u.id}`, { method: 'PATCH', body: { password: pw } })) toast(`已重置 ${u.username} 的密码，对方需要重新登录`)
  }

  return (
    <div className="container">
      <header className="page-head">
        <p className="eyebrow">管理员</p>
        <h1 className="h1">账号</h1>
        <p className="lead muted">每个账号的平台密钥、工作流、批量任务和作品互相隔离，只有本人能看到。</p>
      </header>

      <form className="card form-grid" onSubmit={create} style={{ marginBottom: 24 }}>
        <h2 className="h2">新建账号</h2>
        <label className="field">
          <span>用户名 *</span>
          <input className="input mono" autoComplete="off" value={form.username} placeholder="小写字母、数字、_ . -"
            onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </label>
        <label className="field">
          <span>初始密码 *</span>
          <input className="input" type="text" autoComplete="new-password" value={form.password} placeholder="至少 8 位"
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </label>
        <label className="field">
          <span>角色</span>
          <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </label>
        <button className="btn btn-primary" type="submit" disabled={!form.username || form.password.length < 8}>创建</button>
      </form>

      <div className="card">
        <h2 className="h2">全部账号 · {users.length}</h2>
        <div className="rows" style={{ marginTop: 12 }}>
          {users.map((u) => (
            <div className="row" key={u.id}>
              <div className="row-main">
                <div className="row-title">{u.username}{u.id === me.id ? '（你）' : ''}</div>
                <div className="row-sub">{u.role === 'admin' ? '管理员' : '普通用户'} · 创建于 {new Date(u.createdAt).toLocaleDateString()}</div>
              </div>
              {u.disabled && <span className="chip red">已停用</span>}
              <button className="btn btn-ghost btn-sm" onClick={() => reset(u)}>重置密码</button>
              {u.id !== me.id && <button className="btn btn-ghost btn-sm" onClick={() => toggle(u)}>{u.disabled ? '启用' : '停用'}</button>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
