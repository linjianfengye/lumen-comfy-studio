import { useEffect, useState } from 'react'
import { useStore } from '../lib/store.js'
import { PROVIDERS, providerById } from '../lib/providers.js'
import { createBatch, cancelBatch, clearFinishedBatches, priceOf, USD_TO_CNY, comfyReadyIds, workflowReadyOn } from '../lib/engine.js'
import { Segmented, fmtCny, toast } from '../components/ui.jsx'

const SIZES = {
  image: ['1024x1024', '896x1152', '1152x896', '1344x768'],
  video: ['832x480', '480x832', '1280x720'],
}
const DEFAULT_PROMPTS = {
  image: 'misty alpine lake at dawn, cinematic\nfrosted glass perfume bottle on stone, soft daylight\ncity skyline at blue hour, light haze',
  video: 'slow dolly in over a misty alpine lake at dawn\nsteam rising from a ceramic tea cup, morning light',
}
// Wan / Hunyuan 等视频模型要求帧数为 4n+1
const framesOf = (seconds, fps) => Math.round((seconds * fps) / 4) * 4 + 1

export default function Batch() {
  const st = useStore((s) => s)
  const workflows = st.workflows
  const providers = useStore((s) => s.providers)
  const activeProvider = useStore((s) => s.activeProvider)
  const batches = useStore((s) => s.batches)

  const [kind, setKind] = useState('image')
  const [workflowId, setWorkflowId] = useState('')
  const [text, setText] = useState(DEFAULT_PROMPTS.image)
  const [negative, setNegative] = useState('')
  // 视频参数
  const [vmode, setVmode] = useState('t2v')
  const [seconds, setSeconds] = useState(4)
  const [fps, setFps] = useState(16)
  const [firstFrame, setFirstFrame] = useState(null)
  const [preview, setPreview] = useState(null)
  useEffect(() => {
    if (!firstFrame) return setPreview(null)
    const url = URL.createObjectURL(firstFrame)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [firstFrame])
  const [perPrompt, setPerPrompt] = useState(4)
  const [seedMode, setSeedMode] = useState('step')
  const [seedStart, setSeedStart] = useState(1000)
  const [size, setSize] = useState(SIZES.image[0])
  const [picked, setPicked] = useState([activeProvider])

  const connected = PROVIDERS.filter((p) => providers[p.id].connected)
  const readyIds = comfyReadyIds(st)
  // 只能选已部署 ComfyUI 的平台；可多选，任务轮流分配、并行运算
  const pids = picked.filter((id) => readyIds.includes(id))
  const togglePick = (id) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  const kindWfs = workflows.filter((w) => w.kind === kind)
  const prompts = text.split('\n').map((t) => t.trim()).filter(Boolean)
  const total = prompts.length * perPrompt
  const per = kind === 'video' ? 6 * seconds : 7
  // 各平台分到的任务数 × 单张耗时 + 冷启动；总耗时取最慢的平台，费用逐平台相加
  const plan = pids.map((id, i) => {
    const n = Math.floor(total / pids.length) + (i < total % pids.length ? 1 : 0)
    const gpu = providers[id].gpu
    const secs = n ? n * per * (/H100|A100|A800|L40S|4090/.test(gpu) ? 0.6 : 1) + providerById(id).coldStart : 0
    return { secs, cost: providerById(id).lifecycle === 'local' ? 0 : (priceOf(id, gpu) * USD_TO_CNY * secs) / 3600 }
  })
  const secs = Math.max(0, ...plan.map((x) => x.secs))
  const cost = plan.reduce((a, x) => a + x.cost, 0)

  const switchKind = (k) => { setKind(k); setSize(SIZES[k][0]); setWorkflowId(''); if (text === DEFAULT_PROMPTS[kind]) setText(DEFAULT_PROMPTS[k]) }

  const submit = async () => {
    if (!pids.length) return toast('请选择至少一个已部署 ComfyUI 的平台')
    if (!total) return toast('至少填写一条提示词')
    if (kind === 'video' && vmode === 'i2v' && !firstFrame) return toast('图生视频需要先上传首帧图片')
    const video = kind === 'video' ? { mode: vmode, seconds, fps, frames: framesOf(seconds, fps) } : null
    const r = await createBatch({
      workflowId: workflowId || null, prompts, perPrompt, seedMode, seedStart: Number(seedStart) || 0, kind, size, providerIds: pids,
      negative: negative.trim(), video, firstFrame: video?.mode === 'i2v' ? firstFrame : null,
    })
    if (r) toast(`已提交 ${r.jobs} 个任务到 ${pids.length} 个平台，GPU 将自动开机`)
  }

  return (
    <div className="container">
      <header className="page-head">
        <h1 className="h1">批量生成</h1>
        <p className="lead">一行一条提示词，每条生成多张。提交后自动开机，全部算完后空闲倒计时关机。</p>
      </header>

      <div className="batch-layout">
        <section className="card form-grid" aria-label="新建批量任务">
          <div className="card-head" style={{ marginBottom: 0 }}>
            <h2 className="h3">新建任务</h2>
            <Segmented label="生成类型" value={kind} onChange={switchKind} options={[{ value: 'image', label: '图像' }, { value: 'video', label: '视频' }]} />
          </div>

          <label className="field">
            <span>工作流</span>
            <select className="select" value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
              <option value="">{kind === 'video' ? '默认 · Wan2.1 文生视频' : '默认 · SDXL Base'}</option>
              {kindWfs.map((w) => <option key={w.id} value={w.id}>{w.name}（{pids.filter((id) => workflowReadyOn(st, w, id)).length}/{pids.length} 平台已部署）</option>)}
            </select>
          </label>

          <label className="field">
            <span>提示词 · {prompts.length} 条</span>
            <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
          </label>

          <label className="field">
            <span>反向提示词（可选）</span>
            <input className="input" value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="lowres, blurry, watermark" spellCheck={false} />
          </label>

          {kind === 'video' && (
            <div className="video-panel">
              <div className="card-head" style={{ marginBottom: 0 }}>
                <span className="h3" style={{ fontSize: 16 }}>视频参数</span>
                <Segmented label="视频模式" value={vmode} onChange={setVmode} options={[{ value: 't2v', label: '文生视频' }, { value: 'i2v', label: '图生视频' }]} />
              </div>
              {vmode === 'i2v' && (
                <label className="frame-drop">
                  {preview ? <img src={preview} alt="首帧预览" /> : <span>上传首帧图片<small>写入工作流里的 LoadImage 节点</small></span>}
                  <input type="file" accept="image/*" className="sr-only" onChange={(e) => setFirstFrame(e.target.files[0] || null)} />
                </label>
              )}
              <div className="form-row">
                <label className="field">
                  <span>时长 <b className="tabular">{seconds}s</b></span>
                  <input className="range" type="range" min={2} max={10} value={seconds} onChange={(e) => setSeconds(Number(e.target.value))} />
                </label>
                <div className="field">
                  <span>帧率</span>
                  <Segmented label="帧率" value={fps} onChange={setFps} options={[16, 24, 30].map((v) => ({ value: v, label: `${v} fps` }))} />
                </div>
              </div>
              <p className="hint">共 {framesOf(seconds, fps)} 帧（4n+1，适配 Wan / Hunyuan），自动写入工作流的 length / frame_rate</p>
            </div>
          )}

          <div className="form-row">
            <label className="field">
              <span>每条生成</span>
              <input className="input" type="number" min={1} max={64} value={perPrompt} onChange={(e) => setPerPrompt(Math.max(1, Math.min(64, Number(e.target.value) || 1)))} />
            </label>
            <label className="field">
              <span>尺寸</span>
              <select className="select" value={size} onChange={(e) => setSize(e.target.value)}>
                {SIZES[kind].map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
          </div>

          <div className="form-row">
            <div className="field">
              <span>种子</span>
              <Segmented label="种子模式" value={seedMode} onChange={setSeedMode} options={[{ value: 'step', label: '递增' }, { value: 'fixed', label: '固定' }, { value: 'random', label: '随机' }]} />
            </div>
            <label className="field">
              <span>起始种子</span>
              <input className="input mono" type="number" value={seedStart} disabled={seedMode === 'random'} onChange={(e) => setSeedStart(e.target.value)} />
            </label>
          </div>

          <div className="field">
            <span>算力平台 · 可多选并行（已选 {pids.length} 个）</span>
            <div className="pick" role="group" aria-label="算力平台">
              {connected.length === 0 && <a href="#/providers">尚未连接平台，去连接</a>}
              {connected.map((p) => {
                const ready = readyIds.includes(p.id)
                return (
                  <button key={p.id} type="button" disabled={!ready} aria-pressed={pids.includes(p.id)} onClick={() => togglePick(p.id)}
                    title={ready ? '' : '该平台尚未部署 ComfyUI'}>
                    {p.name} · {providers[p.id].gpu}{ready ? '' : '（未部署 ComfyUI）'}
                  </button>
                )
              })}
            </div>
            {pids.length > 1 && <p className="hint">任务轮流分配到 {pids.length} 个平台同时运算，各平台算完各自空闲关机</p>}
          </div>

          <div className="estimate" aria-label="预估">
            <div><b>{total}</b><span>{kind === 'video' ? '段视频' : '张图'}</span></div>
            <div><b>{Math.ceil(secs / 60)} 分钟</b><span>含冷启动</span></div>
            <div><b>{fmtCny(cost)}</b><span>预估 GPU 费用</span></div>
          </div>

          <button className="btn btn-primary btn-lg" onClick={submit} disabled={!total || !pids.length}>开始生成</button>
        </section>

        <section aria-label="任务队列">
          <div className="card-head">
            <h2 className="h3">队列</h2>
            {batches.some((b) => b.status !== 'active') && <button className="link-btn" onClick={clearFinishedBatches}>清除已结束</button>}
          </div>
          {batches.length === 0 && <div className="card empty">还没有任务</div>}
          <div className="grid" style={{ gap: 12 }}>
            {batches.map((b) => <BatchCard key={b.id} b={b} />)}
          </div>
        </section>
      </div>
    </div>
  )
}

function BatchCard({ b }) {
  const done = b.jobs.filter((j) => j.status === 'done').length
  const failed = b.jobs.filter((j) => j.status === 'failed')
  const status = b.status === 'active' ? { cls: 'blue', text: '进行中' }
    : b.status === 'done' ? (failed.length ? { cls: 'amber', text: `${failed.length} 个失败` } : { cls: 'green', text: '已完成' })
      : { cls: 'red', text: '已取消' }
  return (
    <div className="card card-sm">
      <div className="card-head" style={{ marginBottom: 4 }}>
        <div className="row-title">{b.name}</div>
        <span className={`chip ${status.cls}`}>{status.text}</span>
      </div>
      <div className="row-sub tabular">{(b.providerIds || []).map((id) => providerById(id).name).join(' + ')} · {b.kind === 'video' ? '视频' : '图像'} {b.size} · {done}/{b.jobs.length}</div>
      <div className="job-grid" aria-hidden="true">
        {b.jobs.map((j) => (
          <div key={j.id} className={`job-cell ${j.status}`} title={`${providerById(j.providerId)?.name} · ${j.prompt} · seed ${j.seed}`}>
            {j.status === 'running' && <i style={{ height: `${j.progress * 100}%` }} />}
          </div>
        ))}
      </div>
      {b.status === 'active' && (
        <div style={{ marginTop: 12, textAlign: 'right' }}>
          <button className="btn btn-danger btn-sm" onClick={() => cancelBatch(b.id)}>取消剩余</button>
        </div>
      )}
    </div>
  )
}
