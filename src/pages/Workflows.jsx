import { useRef, useState } from 'react'
import { useStore } from '../lib/store.js'
import { PROVIDERS, providerById } from '../lib/providers.js'
import { importWorkflow, importSample, deployWorkflow, removeWorkflow, setPolicy, modelState, workflowReadyOn, comfyReadyIds, setModelSource } from '../lib/engine.js'
import { totalSize } from '../lib/workflow.js'
import { SAMPLE_SDXL, SAMPLE_WAN_VIDEO } from '../lib/samples.js'
import { fmtGb, toast, Icon, Switch } from '../components/ui.jsx'

const MODEL_STATUS = {
  pending: { cls: '', text: '待部署' },
  queued: { cls: '', text: '排队中' },
  downloading: { cls: 'blue', text: '下载中' },
  cached: { cls: 'green', text: '已就绪' },
  checking: { cls: 'blue', text: '核对中' },
  missing: { cls: 'red', text: '平台缺少' },
  manual: { cls: '', text: '需自备' },
  error: { cls: 'red', text: '缺下载地址' },
}

export default function Workflows() {
  const s = useStore((x) => x)
  const { workflows, policy } = s
  const [selId, setSelId] = useState(null)
  const [over, setOver] = useState(false)
  const inputRef = useRef(null)
  const sel = workflows.find((w) => w.id === selId) || workflows[0]
  const readyIds = comfyReadyIds(s)

  // 解析在后端完成；后端开启自动部署时会立即把模型推到已就绪平台
  const done = (r) => {
    if (!r) return
    setSelId(r.id)
    const where = policy.autoDeployModels && readyIds.length ? `，正在部署到 ${readyIds.length} 个平台` : ''
    toast(`已识别 ${r.models} 个模型、${r.customNodes} 个节点包${where}`)
  }
  const add = async (json, name) => done(await importSample(json, name))
  const readFiles = async (files) => {
    for (const f of files) done(await importWorkflow(f))
  }

  return (
    <div className="container">
      <header className="page-head">
        <h1 className="h1">导入工作流</h1>
        <p className="lead">拖入 ComfyUI 导出的 JSON，Lumen 识别其中的模型和自定义节点，自动部署到所有已就绪的平台。</p>
      </header>

      <div
        className={`dropzone ${over ? 'over' : ''}`}
        role="button" tabIndex={0} aria-label="选择或拖入工作流 JSON 文件"
        onClick={() => inputRef.current.click()}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && inputRef.current.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); readFiles(e.dataTransfer.files) }}
      >
        <div className="dropzone-icon">{Icon.upload}</div>
        <div className="h3">拖入 workflow.json，或点击选择</div>
        <p className="hint">支持 UI 格式与 API 格式（Save → Export / Export (API)）</p>
        <input ref={inputRef} type="file" accept=".json,application/json" multiple hidden onChange={(e) => { readFiles(e.target.files); e.target.value = '' }} />
      </div>

      <div className="toolbar" style={{ marginTop: 16 }}>
        <p className="hint">
          试试示例：
          <button className="link-btn" onClick={() => add(SAMPLE_SDXL, 'SDXL 人像精修.json')}>SDXL 人像精修</button>
          {' · '}
          <button className="link-btn" onClick={() => add(SAMPLE_WAN_VIDEO, 'Wan2.1 文生视频.json')}>Wan2.1 文生视频</button>
        </p>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14 }}>
          导入后自动部署模型（{readyIds.length} 个平台就绪）
          <Switch label="导入后自动部署模型" checked={policy.autoDeployModels} onChange={(v) => setPolicy({ autoDeployModels: v })} />
        </label>
      </div>

      {sel && (
        <section className="section wf-layout">
          <div className="wf-list" aria-label="已导入的工作流">
            {workflows.map((w) => {
              const ok = Object.keys(w.targets || {}).filter((pid) => workflowReadyOn(s, w, pid)).length
              return (
                <button key={w.id} className="wf-item" aria-current={w === sel} onClick={() => setSelId(w.id)}>
                  <div className="row-title">{w.name}</div>
                  <div className="row-sub">{w.kind === 'video' ? '视频' : '图像'} · {w.models.length} 个模型 · {ok} 个平台就绪</div>
                </button>
              )
            })}
          </div>
          <WorkflowDetail key={sel.id} wf={sel} s={s} />
        </section>
      )}
    </div>
  )
}
// 目标平台：每个已连接平台一行，显示该工作流的模型是否已部署到那里
function WorkflowDetail({ wf, s }) {
  const connected = PROVIDERS.filter((p) => s.providers[p.id].connected)
  const [viewPid, setViewPid] = useState(() => connected.find((p) => s.providers[p.id].comfy.status === 'ready')?.id || connected[0]?.id)
  const deployable = connected.filter((p) => s.providers[p.id].comfy.status === 'ready' && !workflowReadyOn(s, wf, p.id) && wf.targets?.[p.id]?.status !== 'deploying')
  const pid = viewPid && s.providers[viewPid]?.connected ? viewPid : connected[0]?.id

  const targetInfo = (p) => {
    const comfy = s.providers[p.id].comfy.status
    if (comfy !== 'ready') return { cls: '', text: comfy === 'deploying' ? 'ComfyUI 部署中' : p.id === 'local' ? '未连接' : 'ComfyUI 未部署' }
    const mode = s.providers[p.id].modelMode
    if (mode === 'manual') return { cls: '', text: '模型需自备' }
    if (mode === 'check') {
      const l = wf.checks?.[p.id]
      if (!l) return { cls: '', text: '未核对' }
      if (l.status === 'checking') return { cls: 'blue', text: '核对中' }
      if (l.status === 'pending') return { cls: '', text: '开机后核对' }
      if (l.status === 'error') return { cls: 'red', text: '核对失败' }
      return l.status === 'ready' ? { cls: 'green', text: '已就绪' } : { cls: 'amber', text: `缺 ${l.missingModels.length} 模型 · ${l.missingNodes.length} 节点` }
    }
    if (workflowReadyOn(s, wf, p.id)) return { cls: 'green', text: '已就绪' }
    const t = wf.targets?.[p.id]
    if (t?.status === 'deploying') return { cls: 'blue', text: t.step || '部署中' }
    if (t?.status === 'error') return { cls: 'red', text: '部署失败' }
    return { cls: '', text: '未部署' }
  }
  const sizeDone = (p) => totalSize(wf.models.filter((m) => modelState(s, wf, m, p.id).status === 'cached'))
  const total = totalSize(wf.models)

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h2 className="h2">{wf.name}</h2>
          <div className="chips" style={{ marginTop: 8 }}>
            <span className="chip">{wf.format === 'api' ? 'API 格式' : 'UI 格式'}</span>
            <span className="chip">{wf.kind === 'video' ? '视频工作流' : '图像工作流'}</span>
            <span className="chip">{wf.models.length} 个模型 · {fmtGb(total)}</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => removeWorkflow(wf.id)}>移除</button>
          <button className="btn btn-primary btn-sm" disabled={!deployable.length}
            onClick={() => { deployWorkflow(wf.id, deployable.map((p) => p.id)); toast(`开始部署到 ${deployable.length} 个平台`) }}>
            {deployable.length ? `一键部署到 ${deployable.length} 个平台` : '无可部署平台'}
          </button>
        </div>
      </div>

      <p className="eyebrow" style={{ marginTop: 24 }}>目标平台 · 点击查看该平台的模型明细</p>
      <div className="rows">
        {connected.length === 0 && <p className="muted">还没有连接平台，<a href="#/providers">去连接</a></p>}
        {connected.map((p) => {
          const info = targetInfo(p)
          const comfyReady = s.providers[p.id].comfy.status === 'ready'
          return (
            <div className="row target-row" key={p.id} role="button" tabIndex={0} aria-pressed={p.id === pid}
              onClick={() => setViewPid(p.id)} onKeyDown={(e) => e.key === 'Enter' && setViewPid(p.id)}>
              <div className="mark light" style={{ width: 32, height: 32, fontSize: 14, borderRadius: 9 }} aria-hidden="true">{p.mark}</div>
              <div className="row-main">
                <div className="row-title">{p.name}</div>
                <div className="row-sub tabular">{fmtGb(sizeDone(p))} / {fmtGb(total)}</div>
              </div>
              <div className="model-bar"><div className={`bar ${info.cls === 'green' ? 'green' : ''}`}><i style={{ width: `${total ? (sizeDone(p) / total) * 100 : 100}%` }} /></div></div>
              <span className={`chip ${info.cls}`}>{info.text}</span>
              {!comfyReady
                ? <a className="btn btn-ghost btn-sm" href="#/providers" onClick={(e) => e.stopPropagation()}>去部署 ComfyUI</a>
                : s.providers[p.id].modelMode === 'check'
                  ? <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); deployWorkflow(wf.id, [p.id]) }}>{wf.checks?.[p.id] ? '重新核对' : '核对模型'}</button>
                  : ['未部署', '部署失败'].includes(info.text) && <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); deployWorkflow(wf.id, [p.id]) }}>{info.text === '部署失败' ? '重试' : '部署'}</button>}
            </div>
          )
        })}
      </div>

      {pid && (
        <>
          <p className="eyebrow" style={{ marginTop: 28 }}>模型 · {providerById(pid).name}</p>
          {wf.checks?.[pid]?.status === 'missing' && (
            <p className="hint" style={{ color: 'var(--amber)', marginBottom: 8 }}>
              该平台不会自动下载：请把缺少的模型放到 ComfyUI/models 对应目录{wf.checks[pid].missingNodes.length ? `，并用 ComfyUI-Manager 安装缺少的节点（${wf.checks[pid].missingNodes.join('、')}）` : ''}，然后点「重新核对」
            </p>
          )}
          {wf.checks?.[pid]?.status === 'error' && <p className="hint" style={{ color: 'var(--red)' }}>{wf.checks[pid].error}</p>}
          {wf.targets?.[pid]?.status === 'error' && <p className="hint" style={{ color: 'var(--red)' }}>{wf.targets[pid].error}</p>}
          {s.providers[pid].modelMode === 'manual' && <p className="hint">该平台无法由 Lumen 下载模型，请预置在镜像或网络卷中。</p>}
          <div className="rows">
            {wf.models.length === 0 && <p className="muted">未识别到模型文件</p>}
            {wf.models.map((m) => {
              const st = modelState(s, wf, m, pid)
              return (
                <div className="row" key={m.name}>
                  <div className="row-main">
                    <div className="row-title mono">{m.name}</div>
                    <div className="row-sub">models/{m.dir}{m.source ? ' · Hugging Face' : ' · 来源待指定'}</div>
                  </div>
                  <div className="model-bar"><div className={`bar ${st.status === 'cached' ? 'green' : ''}`}><i style={{ width: `${st.progress * 100}%` }} /></div></div>
                  <span className="model-size">{fmtGb(m.size)}</span>
                  {!m.source && s.providers[pid].modelMode === 'download'
                    ? <button className="btn btn-ghost btn-sm" onClick={() => { const u = window.prompt(`${m.name} 的下载地址（http/https 直链）`); if (u) setModelSource(wf.id, m.name, u.trim()) }}>填写下载地址</button>
                    : <span className={`chip ${MODEL_STATUS[st.status].cls}`}>{MODEL_STATUS[st.status].text}</span>}
                </div>
              )
            })}
          </div>
        </>
      )}

      <p className="eyebrow" style={{ marginTop: 28 }}>自定义节点 · {wf.customNodes.length} 个包（随模型一起安装）</p>
      <div className="rows">
        {wf.customNodes.length === 0 && <p className="muted">只用到 ComfyUI 内置节点</p>}
        {wf.customNodes.map((c) => (
          <div className="row" key={c.pack}>
            <div className="row-main">
              <div className="row-title">{c.pack}</div>
              <div className="row-sub mono">{c.types.join(', ')}</div>
            </div>
            {pid && <span className={`chip ${workflowReadyOn(s, wf, pid) ? 'green' : ''}`}>{workflowReadyOn(s, wf, pid) ? '已安装' : '待安装'}</span>}
          </div>
        ))}
      </div>

      {wf.prompts.length > 0 && (
        <>
          <p className="eyebrow" style={{ marginTop: 28 }}>提示词节点</p>
          <div className="rows">
            {wf.prompts.map((p) => (
              <div className="row" key={p.nodeId}>
                <div className="row-main">
                  <div className="row-sub">{p.title} · #{p.nodeId}</div>
                  <div>{p.text}</div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
