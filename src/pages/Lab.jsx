import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../lib/store.js'
import { PROVIDERS } from '../lib/providers.js'
import { createBatch, comfyReadyIds, setCompare } from '../lib/engine.js'
import { PRESETS, buildCube, mixPreset } from '../lib/colorlab.js'
import { Media, Segmented, toast } from '../components/ui.jsx'

const clamp01 = (v) => Math.min(Math.max(v, 0), 1)

// 规则版提示词优化（演示）：补齐画质、光线、镜头三类描述，去重。接入后端后换成大模型改写
const BOOSTS = [
  { test: /masterpiece|best quality|high detail|highly detailed|8k|4k/i, add: 'highly detailed, sharp focus' },
  { test: /light|lighting|sunlight|golden hour|blue hour|dawn|dusk/i, add: 'soft natural lighting' },
  { test: /cinematic|35mm|50mm|85mm|lens|bokeh|depth of field/i, add: 'cinematic composition, 35mm lens, shallow depth of field' },
  { test: /color grad|film|tones/i, add: 'subtle film color grading' },
]
export function optimizePrompt(text) {
  const parts = text.split(/[,，\n]/).map((t) => t.trim()).filter(Boolean)
  const seen = new Set()
  const out = parts.filter((p) => !seen.has(p.toLowerCase()) && seen.add(p.toLowerCase()))
  for (const b of BOOSTS) if (!b.test.test(text)) out.push(b.add)
  return out.join(', ')
}

const segs = (t = '') => t.split(/[,，]/).map((x) => x.trim()).filter(Boolean)
function PromptDiff({ base, next }) {
  const have = new Set(segs(base).map((x) => x.toLowerCase()))
  return (
    <span className="diff">
      {segs(next).map((x, i) => (
        <span key={i}>{i > 0 && ', '}{have.has(x.toLowerCase()) ? x : <mark>{x}</mark>}</span>
      ))}
    </span>
  )
}

function FilterDef({ id, curves }) {
  return (
    <filter id={id} colorInterpolationFilters="sRGB">
      <feColorMatrix type="saturate" values={curves.sat} />
      <feComponentTransfer>
        <feFuncR type="table" tableValues={curves.r.join(' ')} />
        <feFuncG type="table" tableValues={curves.g.join(' ')} />
        <feFuncB type="table" tableValues={curves.b.join(' ')} />
      </feComponentTransfer>
    </filter>
  )
}

function CurvePlot({ curves }) {
  const path = (t) => t.map((v, i) => `${i ? 'L' : 'M'}${(i / (t.length - 1)) * 100} ${100 - v * 100}`).join(' ')
  return (
    <svg className="curve-plot" viewBox="-2 -2 104 104" aria-hidden="true">
      {[25, 50, 75].map((v) => <path key={v} d={`M${v} 0V100M0 ${v}H100`} className="curve-grid" />)}
      <path d="M0 100L100 0" className="curve-id" />
      <path d={path(curves.r)} stroke="#ff5f57" />
      <path d={path(curves.g)} stroke="#3ddc84" />
      <path d={path(curves.b)} stroke="#4c9bff" />
    </svg>
  )
}
// 两段视频对比时让 B 跟随 A 的播放进度
function useVideoSync(aRef, bRef, deps) {
  useEffect(() => {
    const t = setInterval(() => {
      const a = aRef.current, b = bRef.current
      if (a?.tagName === 'VIDEO' && b?.tagName === 'VIDEO' && Math.abs(a.currentTime - b.currentTime) > 0.15) b.currentTime = a.currentTime
    }, 500)
    return () => clearInterval(t)
  }, deps)
}

export default function Lab() {
  const gallery = useStore((s) => s.gallery)
  const compare = useStore((s) => s.compare)
  const a = gallery.find((g) => g.id === compare.a) || gallery[0]
  const b = gallery.find((g) => g.id === compare.b) || gallery[1] || a
  const [slot, setSlot] = useState('b')
  const [presetId, setPresetId] = useState('raw')
  const [strength, setStrength] = useState(100)
  const [target, setTarget] = useState('b')
  const [split, setSplit] = useState(50)
  const [dragging, setDragging] = useState(false)
  const stageRef = useRef(null)
  const aRef = useRef(null)
  const bRef = useRef(null)
  useVideoSync(aRef, bRef, [a?.id, b?.id])
  const preset = PRESETS.find((p) => p.id === presetId)
  const curves = useMemo(() => mixPreset(preset, strength / 100), [preset, strength])
  const filterFor = (side) => (presetId !== 'raw' && (target === 'both' || target === side) ? 'url(#lab-live)' : 'none')

  const moveTo = (x) => {
    const r = stageRef.current.getBoundingClientRect()
    setSplit(Math.round(clamp01((x - r.left) / r.width) * 1000) / 10)
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([buildCube(preset.en, curves)], { type: 'text/plain' }))
    const el = document.createElement('a')
    el.href = url
    el.download = `lumen-${preset.id}-${strength}.cube`
    el.click()
    URL.revokeObjectURL(url)
    toast(`已导出 ${preset.name} LUT，可导入剪映 / PR / 达芬奇`)
  }

  if (!a) return <div className="container"><div className="card empty section">作品库还没有作品</div></div>

  return (
    <div className="container">
      <header className="page-head">
        <h1 className="h1">色彩对比</h1>
        <p className="lead">同一种子分别用原提示词和优化后的提示词出图，拖动中线对比；再套色彩预设看调色效果，满意可导出 .cube LUT 给视频后期。</p>
      </header>

      <Optimizer />

      <section className="lab-card section" aria-label="对比台">
        <svg width="0" height="0" className="sr-only" aria-hidden="true">
          <defs>
            <FilterDef id="lab-live" curves={curves} />
            {PRESETS.map((p) => <FilterDef key={p.id} id={`lab-${p.id}`} curves={mixPreset(p, 1)} />)}
          </defs>
        </svg>

        <div
          className={`lab-stage ${dragging ? 'is-dragging' : ''}`}
          ref={stageRef}
          onPointerDown={(e) => { setDragging(true); e.currentTarget.setPointerCapture(e.pointerId); moveTo(e.clientX) }}
          onPointerMove={(e) => dragging && moveTo(e.clientX)}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
        >
          <Media item={a} mediaRef={aRef} className="media" alt={`对比 A：${a.prompt}`} style={{ filter: filterFor('a') }} />
          <Media item={b} mediaRef={bRef} className="media" alt="" style={{ filter: filterFor('b'), clipPath: `inset(0 0 0 ${split}%)` }} />
          <span className="lab-tag left">A · {a.prompt}</span>
          <span className="lab-tag right">B · {b.prompt}</span>
          <div className="lab-divider" style={{ left: `${split}%` }}><span className="lab-handle" aria-hidden="true"><i /><i /></span></div>
          <input className="lab-range" type="range" min="0" max="100" step="0.5" value={split} onChange={(e) => setSplit(Number(e.target.value))} aria-label="对比分割线位置" />
        </div>

        <p style={{ margin: '16px 0 0', fontSize: 13, color: '#a1a1a6' }}>
          B 相对 A 新增：<span style={{ color: '#f5f5f7' }}><PromptDiff base={a.prompt} next={b.prompt} /></span>
          {a.seed === b.seed && <span> · 同种子 {a.seed}</span>}
        </p>

        <div className="lab-panel">
          <div className="lab-presets" role="radiogroup" aria-label="色彩预设">
            {PRESETS.map((p) => (
              <button key={p.id} role="radio" aria-checked={p.id === presetId} onClick={() => setPresetId(p.id)}>
                <span className="preset-thumb"><Media item={target === 'a' ? a : b} style={{ filter: `url(#lab-${p.id})` }} /></span>
                <strong>{p.name}</strong>
                <small>{p.en}</small>
              </button>
            ))}
          </div>
          <div className="lab-controls">
            <div className="lab-note">
              <CurvePlot curves={curves} />
              <p><strong>{preset.name}</strong>{preset.note}</p>
            </div>
            <label className="lab-slider">
              <span>强度 <b>{strength}%</b></span>
              <input type="range" min="0" max="100" value={strength} onChange={(e) => setStrength(Number(e.target.value))} disabled={presetId === 'raw'} />
            </label>
            <div className="lab-seg" role="group" aria-label="调色作用于">
              {[['a', '只调 A'], ['b', '只调 B'], ['both', 'A 和 B']].map(([v, l]) => (
                <button key={v} aria-pressed={target === v} onClick={() => setTarget(v)}>{l}</button>
              ))}
            </div>
            <button className="btn btn-ghost" onClick={download} disabled={presetId === 'raw'}>导出 .cube LUT</button>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <h2 className="h3">从作品库挑选</h2>
          <Segmented label="放入槽位" value={slot} onChange={setSlot} options={[{ value: 'a', label: '放入 A' }, { value: 'b', label: '放入 B' }]} />
        </div>
        <div className="picker" role="group" aria-label="作品">
          {gallery.map((g) => {
            const is = g.id === a.id ? 'a' : g.id === b.id ? 'b' : ''
            return (
              <button key={g.id} className={is ? `is-${is}` : ''} onClick={() => setCompare(slot, g.id)} aria-label={`放入 ${slot.toUpperCase()}：${g.prompt}`}>
                <Media item={g} />
                {is && <span className="slot">{is.toUpperCase()}</span>}
              </button>
            )
          })}
        </div>
      </section>
    </div>
  )
}

// 原提示词 vs 优化后：同种子各出一张，完成后自动放进对比台
function Optimizer() {
  const st = useStore((s) => s)
  const ready = PROVIDERS.filter((p) => comfyReadyIds(st).includes(p.id))
  const [orig, setOrig] = useState('a lake in the mountains, morning')
  const [opt, setOpt] = useState('')
  const [seed, setSeed] = useState(20240)
  const [pid, setPid] = useState('')
  const provider = ready.find((p) => p.id === pid)?.id || ready[0]?.id

  const run = async () => {
    if (!provider) return toast('先在算力平台连接并部署一个平台')
    const better = opt.trim() || optimizePrompt(orig)
    setOpt(better)
    const r = await createBatch({ workflowId: null, prompts: [orig.trim(), better], perPrompt: 1, seedMode: 'fixed', seedStart: Number(seed) || 0, kind: 'image', size: '1344x768', providerIds: [provider], compare: true })
    if (r) toast('已提交对比任务，两张完成后自动放进对比台')
  }

  return (
    <section className="card" aria-label="提示词优化对比">
      <div className="card-head">
        <div>
          <p className="eyebrow">提示词优化</p>
          <h2 className="h2">同一种子，换一句提示词</h2>
        </div>
        <span className="demo-badge">规则版优化 · 接入大模型后更智能</span>
      </div>
      <div className="grid grid-2">
        <label className="field">
          <span>原提示词（A）</span>
          <textarea className="textarea" style={{ minHeight: 96 }} value={orig} onChange={(e) => setOrig(e.target.value)} spellCheck={false} />
        </label>
        <label className="field">
          <span>优化后（B）· 可手改</span>
          <textarea className="textarea" style={{ minHeight: 96 }} value={opt} placeholder="点「优化」自动生成" onChange={(e) => setOpt(e.target.value)} spellCheck={false} />
        </label>
      </div>
      <div className="toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setOpt(optimizePrompt(orig))}>优化</button>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 14 }}>
            种子 <input className="input mono" style={{ width: 110, padding: '6px 10px' }} type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          <select className="select" style={{ width: 'auto', padding: '6px 10px' }} value={provider || ''} onChange={(e) => setPid(e.target.value)} aria-label="运行平台">
            {ready.length === 0 && <option value="">没有就绪的平台</option>}
            {ready.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <button className="btn btn-primary" onClick={run} disabled={!orig.trim() || !provider}>生成对比</button>
      </div>
    </section>
  )
}
