import { useState } from 'react'
import { useSession, useStore } from '../lib/store.js'
import { connectLocalBridge, testLocalBridge } from '../lib/localBridge.js'
import { PROVIDERS, providerById, LIFECYCLE_LABEL, comfyUiLink } from '../lib/providers.js'
import { connectProvider, testProvider, disconnectProvider, setGpu, setActiveProvider, setPolicy, startProvider, stopProvider, deployComfy } from '../lib/engine.js'
import { Modal, Switch, powerInfo, fmtClock, toast } from '../components/ui.jsx'

export default function Providers() {
  const [editing, setEditing] = useState(null)
  const [region, setRegion] = useState('all')
  const list = PROVIDERS.filter((p) => region === 'all' || p.region === region || p.region === '本机')

  return (
    <div className="container">
      <header className="page-head">
        <h1 className="h1">算力平台</h1>
        <p className="lead">Serverless 平台由平台自身缩容到零；按实例计费的平台由 Lumen 在空闲后调用关机接口。本地 ComfyUI 直接连。</p>
      </header>

      <DeployAllBar />

      <div className="section"><PolicyCard /></div>

      <div className="toolbar section">
        <h2 className="h2">选择平台</h2>
        <div className="segmented" role="group" aria-label="地区">
          {['all', '海外', '国内'].map((r) => (
            <button key={r} aria-pressed={region === r} onClick={() => setRegion(r)}>{r === 'all' ? '全部' : r}</button>
          ))}
        </div>
      </div>

      <div className="bento">
        {list.map((p, i) => <ProviderCard key={p.id} p={p} featured={i === 0 && region === 'all'} onEdit={() => setEditing(p.id)} />)}
      </div>

      {editing && <ConnectModal id={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

// 一键把 ComfyUI 部署到所有已连接、尚未部署的平台
function DeployAllBar() {
  const providers = useStore((s) => s.providers)
  const connected = PROVIDERS.filter((p) => providers[p.id].connected)
  const ready = connected.filter((p) => providers[p.id].comfy.status === 'ready')
  const deploying = connected.filter((p) => providers[p.id].comfy.status === 'deploying')
  const todo = connected.filter((p) => ['none', 'error'].includes(providers[p.id].comfy.status))

  return (
    <section className="card deploy-bar" aria-label="一键部署 ComfyUI">
      <div>
        <p className="eyebrow">ComfyUI 部署</p>
        <h2 className="h2">已连接 {connected.length} 个平台，{ready.length} 个已部署 ComfyUI</h2>
        <div className="chips" style={{ marginTop: 12 }}>
          {connected.map((p) => {
            const st = providers[p.id].comfy.status
            return <span key={p.id} className={`chip ${st === 'ready' ? 'green' : st === 'deploying' ? 'blue' : ''}`}>{p.name} · {st === 'ready' ? '已部署' : st === 'deploying' ? `部署中 ${Math.round(providers[p.id].comfy.progress * 100)}%` : '未部署'}</span>
          })}
          {connected.length === 0 && <span className="muted">还没有连接任何平台，先在下方选一个连接</span>}
        </div>
      </div>
      <button className="btn btn-primary btn-lg" disabled={!todo.length} onClick={() => { deployComfy(todo.map((p) => p.id)); toast(`开始在 ${todo.length} 个平台部署 ComfyUI`) }}>
        {todo.length ? `一键部署到 ${todo.length} 个平台` : deploying.length ? '部署中…' : '全部已部署'}
      </button>
    </section>
  )
}

function PolicyCard() {
  const policy = useStore((s) => s.policy)
  const active = useStore((s) => s.activeProvider)
  const rt = useStore((s) => s.runtime[s.activeProvider])
  const info = powerInfo(rt, active)
  const shown = rt.power === 'idle' ? rt.idleLeft : policy.idleMinutes * 60

  return (
    <section className="card policy" aria-label="空闲自动关机策略">
      <div>
        <p className="eyebrow">空闲自动关机</p>
        <div className="policy-row">
          <div>
            <div className="h3">自动关机</div>
            <p className="hint">队列清空后开始倒计时，到点释放 GPU</p>
          </div>
          <Switch label="自动关机" checked={policy.autoShutdown} onChange={(v) => setPolicy({ autoShutdown: v })} />
        </div>
        <div className="policy-row" style={{ display: 'grid', gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span className="h3">空闲等待</span>
            <span className="tabular">{policy.idleMinutes} 分钟</span>
          </div>
          <input className="range" type="range" min={1} max={30} value={policy.idleMinutes} aria-label="空闲等待分钟数"
            onChange={(e) => setPolicy({ idleMinutes: Number(e.target.value) })} disabled={!policy.autoShutdown} />
          <p className="hint">Modal 部署时写入 scaledown_window（改动后下次部署生效）；Vast 由 Lumen 到点关机。越短越省钱，越长连续出图越少冷启动</p>
        </div>
        <div className="policy-row">
          <div>
            <div className="h3">冷启动预热</div>
            <p className="hint">开启后立即拉起当前平台，第一张图不用等冷启动</p>
          </div>
          <Switch label="冷启动预热" checked={policy.prewarm} onChange={(v) => setPolicy({ prewarm: v })} />
        </div>
        <div className="policy-row">
          <div>
            <div className="h3">部署后健康检查</div>
            <p className="hint">关闭时部署完不拉起 GPU，第一次出图时才冷启动；开启会多花一次冷启动和空闲等待的费用</p>
          </div>
          <Switch label="部署后健康检查" checked={policy.verifyDeploy} onChange={(v) => setPolicy({ verifyDeploy: v })} />
        </div>
        <div className="policy-row">
          <div>
            <div className="h3">每日预算</div>
            <p className="hint">今日花费达到上限后不再自动开机</p>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            ¥<input className="input tabular" style={{ width: 90 }} type="number" min={0} value={policy.dailyBudget} aria-label="每日预算（元）"
              onChange={(e) => setPolicy({ dailyBudget: Math.max(0, Number(e.target.value) || 0) })} />
          </label>
        </div>
      </div>
      <div style={{ textAlign: 'center' }}>
        <div className="big-timer" aria-live="polite">{fmtClock(shown)}</div>
        <p className="muted" style={{ margin: '12px 0 20px' }}>
          <span className={`dot ${info.dot}`} style={{ display: 'inline-block', marginRight: 8 }} />
          {providerById(active).name} · {info.label}
        </p>
        {rt.power === 'off'
          ? <button className="btn btn-ghost" onClick={() => startProvider(active)}>现在开机</button>
          : <button className="btn btn-danger" disabled={rt.power === 'stopping'} onClick={() => stopProvider(active)}>立即关机</button>}
      </div>
    </section>
  )
}

function ProviderCard({ p, featured, onEdit }) {
  const st = useStore((s) => s.providers[p.id])
  const isActive = useStore((s) => s.activeProvider === p.id)
  const rt = useStore((s) => s.runtime[p.id])
  const info = powerInfo(rt, p.id)

  return (
    <article className={`card provider-card ${featured ? 'featured' : ''}`} style={isActive ? { borderColor: 'var(--blue)' } : undefined}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
        <div className={`mark ${st.connected ? '' : 'light'}`} aria-hidden="true">{p.mark}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 className="h3">{p.name}</h3>
          <div className="row-sub">{p.region} · {LIFECYCLE_LABEL[p.lifecycle]}</div>
        </div>
        {st.connected && <span className="row-sub" style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span className={`dot ${info.dot}`} />{info.label}</span>}
      </div>
      <p className="muted" style={{ margin: 0 }}>{p.desc}</p>
      <div className="chips">
        {st.connected && st.demo && <span className="chip amber">演示模式</span>}
        {!p.adapter && <span className="chip">真实接入开发中</span>}
        {p.tags.map((t) => <span key={t} className={`chip ${/免费|试用|赠送|体验/.test(t) ? 'green' : ''}`}>{t}</span>)}
      </div>
      <div className="gpu-select" role="group" aria-label={`${p.name} GPU 型号`}>
        {p.gpus.map((g) => (
          <button key={g.id} aria-pressed={st.gpu === g.id} onClick={() => setGpu(p.id, g.id)}>
            {g.id}{g.price ? ` · $${g.price}/h` : ''}
          </button>
        ))}
      </div>
      <p className="hint">关机方式：{p.shutdown}</p>
      {rt.error && rt.power === 'off' && <p className="hint" style={{ color: 'var(--red)' }}>上次开机失败：{rt.error}</p>}
      {st.connected && <ComfyStatus p={p} st={st} onEdit={onEdit} />}
      <div className="card-foot">
        {st.connected
          ? <>
              {isActive ? <span className="chip blue">当前默认</span> : <button className="link-btn" onClick={() => setActiveProvider(p.id)}>设为默认</button>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-ghost btn-sm" onClick={onEdit}>配置</button>
                <button className="btn btn-danger btn-sm" onClick={() => disconnectProvider(p.id)}>断开</button>
              </div>
            </>
          : <button className="btn btn-dark btn-sm" onClick={onEdit}>{p.lifecycle === 'local' ? '连接本地 ComfyUI' : '连接并部署 ComfyUI'}</button>}
      </div>
    </article>
  )
}

// 单个平台的 ComfyUI 部署区：未部署 → 一键部署；部署中 → 后端上报的步骤与进度；已就绪 → 打开界面
function ComfyStatus({ p, st, onEdit }) {
  const c = st.comfy
  const power = useStore((s) => s.runtime[p.id].power)
  if (c.status === 'ready') {
    const link = st.demo ? null : comfyUiLink(p, st)
    // 按实例计费的平台，打开界面前先开机
    const open = () => { if (p.lifecycle === 'instance' && power === 'off') { startProvider(p.id); toast('正在开机，界面加载可能需要几分钟') } }
    const i = c.info || {}
    return (
      <div className="comfy-box ready" style={{ flexWrap: 'wrap' }}>
        <span className="dot on" />
        <span style={{ flex: 1, minWidth: 0 }}>
          {p.lifecycle === 'local' ? `已连接 · ${i.device || '本机'}${i.vram ? ` · ${i.vram} GB` : ''}` : 'ComfyUI 已部署，可接收任务'}
          {i.version && i.version !== 'demo' && <span className="row-sub" style={{ display: 'block', fontWeight: 400 }}>ComfyUI {i.version}{i.nodeCount ? ` · ${i.nodeCount} 个节点` : ''}{i.checkpoints != null ? ` · ${i.checkpoints} 个 checkpoint` : ''}</span>}
          {i.warning && <span className="row-sub" style={{ display: 'block', fontWeight: 400, color: 'var(--amber)' }}>{i.warning}</span>}
        </span>
        {link
          ? <a className="btn btn-primary btn-sm" href={link} target="_blank" rel="noreferrer" onClick={open}>打开 ComfyUI 界面 ↗</a>
          : st.demo
            ? <span className="row-sub" style={{ fontWeight: 400 }}>演示模式没有真实界面</span>
            : p.ui === 'field'
              ? <button className="link-btn" onClick={onEdit}>填写界面地址</button>
              : <span className="row-sub" style={{ fontWeight: 400 }}>{p.ui ? '填写有效的接口地址后可打开界面' : '该平台只提供 API，可视化编辑请用本地 ComfyUI'}</span>}
      </div>
    )
  }
  if (c.status === 'error') {
    return (
      <div className="comfy-box" style={{ background: 'var(--red-soft)' }}>
        <p className="hint" style={{ color: 'var(--red)', margin: '0 0 10px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{c.error}</p>
        <button className="btn btn-ghost btn-sm" onClick={() => deployComfy([p.id])}>{p.lifecycle === 'local' ? '重新检测' : '重试部署'}</button>
      </div>
    )
  }
  if (c.status === 'deploying') {
    return (
      <div className="comfy-box">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, gap: 12 }}>
          <span>正在部署 ComfyUI{c.step ? ` · ${c.step}` : ''}</span>
          <span className="tabular muted">{Math.round(c.progress * 100)}%</span>
        </div>
        <div className="bar"><i style={{ width: `${c.progress * 100}%` }} /></div>
        {p.id === 'modal' && !st.demo && <p className="hint" style={{ marginTop: 8 }}>首次构建镜像约 5–10 分钟，可以离开这个页面</p>}
      </div>
    )
  }
  return (
    <div className="comfy-box">
      <div style={{ marginBottom: 10 }}>尚未部署 ComfyUI</div>
      <button className="btn btn-primary btn-sm" onClick={() => deployComfy([p.id])}>
        {p.lifecycle === 'local' ? '检测本地 ComfyUI' : '一键部署 ComfyUI'}
      </button>
    </div>
  )
}

function ConnectModal({ id, onClose }) {
  const p = providerById(id)
  const saved = useStore((s) => s.providers[id])
  // 已保存的密钥由后端返回掩码；原样提交时后端保留原值
  const [cfg, setCfg] = useState(() => ({ ...(id === 'local' ? { endpoint: 'http://127.0.0.1:8188' } : {}), ...saved.config }))
  const [demo, setDemo] = useState(saved.connected ? saved.demo : !p.adapter)
  const [test, setTest] = useState(null)
  const [autoDeploy, setAutoDeploy] = useState(true)
  const fields = p.fields.filter((f) => f.key !== 'via')
  // 在线版：本机 ComfyUI 由浏览器直连，服务器不访问 127.0.0.1
  const server = useSession().server
  const bridge = id === 'local' && !demo && !!server?.public

  const runTest = async () => {
    setTest({ state: 'loading' })
    if (bridge) {
      try { setTest({ state: 'ok', text: await testLocalBridge(cfg.endpoint || '') }) } catch (x) { setTest({ state: 'err', text: x.message }) }
      return
    }
    const r = await testProvider(id, cfg, demo)
    setTest(r ? { state: r.ok ? 'ok' : 'err', text: r.text } : null)
  }

  const save = async (e) => {
    e.preventDefault()
    if (bridge) {
      try {
        const info = await connectLocalBridge(cfg.endpoint || '')
        toast(`已连接本机 ComfyUI · ${info.device}`)
        onClose()
      } catch (x) { setTest({ state: 'err', text: x.message }) }
      return
    }
    const r = await connectProvider(id, cfg, { demo, deploy: autoDeploy })
    if (!r) return
    toast(autoDeploy ? `已连接 ${p.name}，开始部署 ComfyUI` : `已连接 ${p.name}`)
    onClose()
  }

  return (
    <Modal onClose={onClose} label={`连接 ${p.name}`}>
      <form onSubmit={save} className="form-grid">
        <div>
          <h2 className="h2">连接 {p.name}</h2>
          <p className="muted" style={{ margin: '6px 0 0' }}>{bridge ? '由浏览器直连你电脑上的 ComfyUI，出图结果自动存进作品库。' : p.hint || p.desc}</p>
        </div>
        <div className="policy-row" style={{ padding: 0, borderTop: 0 }}>
          <div>
            <div style={{ fontWeight: 500 }}>演示模式</div>
            <p className="hint">{p.adapter ? '不连接真实平台，用示例图模拟完整流程' : '该平台的真实接入还在开发中，目前只能演示'}</p>
          </div>
          <Switch label="演示模式" checked={demo} onChange={(v) => p.adapter && setDemo(v)} />
        </div>
        {!demo && fields.map((f) => (
          <label key={f.key} className="field">
            <span>{f.label}{f.optional ? '' : ' *'}</span>
            <input className={`input ${f.secret ? '' : 'mono'}`} type={f.secret ? 'password' : 'text'} autoComplete="off"
              placeholder={f.placeholder} value={cfg[f.key] || ''} onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />
          </label>
        ))}
        {bridge && (
          <div className="hint">
            <p style={{ margin: '0 0 6px' }}>在线版由这个网页直接访问你电脑上的 ComfyUI，服务器不会连你的本机。ComfyUI 需要允许本网站访问，请这样启动：</p>
            <pre className="code" style={{ margin: 0 }}>{`python main.py --enable-cors-header ${window.location.origin}`}</pre>
            <p style={{ margin: '6px 0 0' }}>出图时要保持本网页打开；Chrome 首次会询问是否允许访问本地网络，请选允许。Safari 可能不支持。</p>
          </div>
        )}
        {!demo && p.fields.some((f) => f.secret) && (
          <p className="hint">{server?.public
            ? '密钥加密保存在服务器上（离线自动开关机需要用到），页面上只显示掩码；服务器运营者在技术上可以解密。'
            : '密钥只发给本机的 Lumen 后端，加密保存在 server/data，页面上只显示掩码。'}</p>
        )}
        {!demo && (
          <div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={runTest} disabled={test?.state === 'loading'}>
              {test?.state === 'loading' ? '测试中…' : '测试连接'}
            </button>
            {test?.text && <p className="hint" role="status" style={{ marginTop: 8, color: test.state === 'ok' ? 'var(--green)' : 'var(--red)', wordBreak: 'break-word' }}>{test.text}</p>}
          </div>
        )}
        {!bridge && (
          <div className="policy-row" style={{ padding: '4px 0 0', borderTop: 0 }}>
            <div>
              <div style={{ fontWeight: 500 }}>连接后一键部署 ComfyUI</div>
              <p className="hint">{p.lifecycle === 'local' ? '检测本机 ComfyUI 并读取节点列表' : id === 'modal' ? '执行 modal deploy：构建镜像、创建模型存储卷、发布 web endpoint' : '检查平台连通性并准备接收任务'}</p>
            </div>
            <Switch label="连接后一键部署 ComfyUI" checked={autoDeploy} onChange={setAutoDeploy} />
          </div>
        )}
        <div className="modal-actions" style={{ marginTop: 8 }}>
          <button type="button" className="btn btn-ghost" onClick={onClose}>取消</button>
          <button type="submit" className="btn btn-primary">保存并连接</button>
        </div>
      </form>
    </Modal>
  )
}
