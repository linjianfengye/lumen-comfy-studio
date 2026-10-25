import { useStore } from '../lib/store.js'
import { PROVIDERS, providerById, LIFECYCLE_LABEL } from '../lib/providers.js'
import { startProvider, stopProvider, deployComfy } from '../lib/engine.js'
import { powerInfo, fmtCny, Icon } from '../components/ui.jsx'

const STEPS = [
  { key: 'queue', title: '任务进队列', text: '批量提交后，调度器发现有待算任务' },
  { key: 'starting', title: '自动开机', text: '拉起 GPU 容器，挂载已缓存的模型卷' },
  { key: 'running', title: '运算', text: '逐张出图，结果回传作品库' },
  { key: 'idle', title: '空闲自动关机', text: '队列清空后倒计时，到点释放 GPU' },
]

// 所有已连接平台的实时状态：每个平台独立开机、运算、关机
function PlatformList() {
  const providers = useStore((s) => s.providers)
  const runtime = useStore((s) => s.runtime)
  const connected = PROVIDERS.filter((p) => providers[p.id].connected)
  return (
    <section className="section card">
      <div className="card-head">
        <div>
          <p className="eyebrow">算力平台</p>
          <h2 className="h2">已连接 {connected.length} 个平台</h2>
        </div>
        <a className="btn btn-ghost btn-sm" href="#/providers">连接更多平台</a>
      </div>
      <div className="rows">
        {connected.map((p) => {
          const info = powerInfo(runtime[p.id], p.id)
          const comfy = providers[p.id].comfy
          return (
            <div className="row" key={p.id}>
              <div className="mark light" style={{ width: 36, height: 36, fontSize: 15, borderRadius: 10 }} aria-hidden="true">{p.mark}</div>
              <div className="row-main">
                <div className="row-title">{p.name} · {providers[p.id].gpu}</div>
                <div className="row-sub">{LIFECYCLE_LABEL[p.lifecycle]}</div>
              </div>
              {comfy.status === 'ready'
                ? <span className="row-sub tabular" style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span className={`dot ${info.dot}`} />{info.label}</span>
                : comfy.status === 'deploying'
                  ? <span className="chip blue">ComfyUI 部署中 {Math.round(comfy.progress * 100)}%</span>
                  : <button className="btn btn-primary btn-sm" onClick={() => deployComfy([p.id])}>一键部署 ComfyUI</button>}
            </div>
          )
        })}
      </div>
    </section>
  )
}

export default function Home() {
  const active = useStore((s) => s.activeProvider)
  const rt = useStore((s) => s.runtime[s.activeProvider])
  const gpu = useStore((s) => s.providers[s.activeProvider].gpu)
  const today = useStore((s) => s.spend.today)
  const budget = useStore((s) => s.policy.dailyBudget)
  const batches = useStore((s) => s.batches)
  const p = providerById(active)
  const info = powerInfo(rt, active)

  const queued = batches.reduce((n, b) => n + b.jobs.filter((j) => j.status === 'queued' || j.status === 'running').length, 0)
  const done = batches.reduce((n, b) => n + b.jobs.filter((j) => j.status === 'done').length, 0)
  const stage = rt.power === 'off' ? (queued ? 'queue' : null) : rt.power === 'stopping' ? 'idle' : rt.power

  return (
    <div className="container">
      <section className="hero">
        <p className="eyebrow">ComfyUI 云端自动化</p>
        <h1 className="display">开机即算，算完即停。</h1>
        <p className="lead">导入工作流，模型自动部署到云端。批量出图或视频时 GPU 自动开启，空闲到点自动关机，一分钱不浪费。</p>
        <div className="hero-actions">
          <a className="btn btn-primary btn-lg" href="#/workflows">导入工作流</a>
          <a href="#/providers">查看算力平台 {Icon.chevron}</a>
        </div>
        <figure className="hero-figure">
          <img src="/samples/lake.webp" alt="晨雾中的高山湖泊，由 SDXL 工作流生成的示例作品" />
        </figure>
      </section>

      <section className="grid grid-3 overview-cards" aria-label="运行概况">
        <div className="card">
          <p className="eyebrow">GPU 状态</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className={`dot ${info.dot}`} />
            <span className="h3">{p.name} · {gpu}</span>
          </div>
          <p className="muted tabular" style={{ margin: 0 }}>{info.label}</p>
          <div style={{ marginTop: 'auto', display: 'flex', gap: 8 }}>
            {rt.power === 'off'
              ? <button className="btn btn-ghost btn-sm" onClick={() => startProvider(active)}>预热开机</button>
              : <button className="btn btn-danger btn-sm" onClick={() => stopProvider(active)} disabled={rt.power === 'stopping'}>立即关机</button>}
            <span className="chip">{LIFECYCLE_LABEL[p.lifecycle]}</span>
          </div>
        </div>
        <div className="card">
          <p className="eyebrow">今日花费</p>
          <div className="stat">{fmtCny(today)}</div>
          <p className="muted" style={{ margin: 0 }}>日预算 {fmtCny(budget)}，超出后暂停自动开机</p>
          <div className="bar" style={{ marginTop: 'auto' }}><i style={{ width: `${Math.min(100, (today / budget) * 100)}%` }} /></div>
        </div>
        <div className="card">
          <p className="eyebrow">任务队列</p>
          <div className="stat">{queued}<small>待算</small></div>
          <p className="muted" style={{ margin: 0 }}>累计完成 {done} 个</p>
          <a href="#/batch" style={{ marginTop: 'auto' }}>新建批量任务 {Icon.chevron}</a>
        </div>
      </section>

      <PlatformList />

      <section className="section card">
        <div className="section-head">
          <p className="eyebrow">自动开关机</p>
          <h2 className="h2">GPU 只在有活干的时候计费</h2>
        </div>
        <ol className="lifecycle" style={{ padding: 0, margin: 0 }}>
          {STEPS.map((s) => (
            <li key={s.key} className={stage === s.key ? 'active' : ''}>
              <span className="h3">{s.title}</span>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
