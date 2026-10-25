import { useState } from 'react'
import { useStore } from '../lib/store.js'
import { providerById } from '../lib/providers.js'
import { removeGalleryItem, setCompare } from '../lib/engine.js'
import { Media, Modal, Segmented, fmtTime, toast } from '../components/ui.jsx'


export default function Gallery() {
  const gallery = useStore((s) => s.gallery)
  const [filter, setFilter] = useState('all')
  const [openId, setOpenId] = useState(null)
  const items = gallery.filter((g) => filter === 'all' || g.kind === filter)
  const open = gallery.find((g) => g.id === openId)

  const toCompare = async (slot) => {
    if (await setCompare(slot, open.id)) toast(`已放入对比 ${slot.toUpperCase()}`)
  }

  return (
    <div className="container">
      <header className="page-head">
        <h1 className="h1">作品库</h1>
        <p className="lead">每张作品都记录了提示词、种子、工作流和算力，方便复现。挑两张放进色彩对比，看优化前后的差别。</p>
      </header>

      <div className="toolbar">
        <Segmented label="类型筛选" value={filter} onChange={setFilter}
          options={[{ value: 'all', label: `全部 ${gallery.length}` }, { value: 'image', label: '图像' }, { value: 'video', label: '视频' }]} />
        <span className="demo-badge">演示平台的作品用示例图占位</span>
      </div>

      {items.length === 0 && <div className="card empty">这里还没有作品</div>}
      <div className="masonry">
        {items.map((g) => (
          <button key={g.id} className="tile" onClick={() => setOpenId(g.id)} aria-label={`查看作品：${g.prompt}`}>
            <Media item={g} style={{ aspectRatio: `${g.w} / ${g.h}` }} />
            <span className="tile-badge chips">
              {g.kind === 'video' && <span className="chip">▶ {g.duration}s{g.fps ? ` · ${g.fps}fps` : ''}</span>}
              {g.sample && <span className="chip">示例</span>}
            </span>
          </button>
        ))}
      </div>

      {open && (
        <Modal onClose={() => setOpenId(null)} className="detail" label="作品详情">
          <div className="detail-media">
            <Media item={open} alt={open.prompt} controls />
          </div>
          <div className="detail-info">
            <div>
              <p className="eyebrow">{open.kind === 'video' ? `视频 · ${open.duration}s${open.fps ? ` · ${open.fps} fps` : ''}` : '图像'}</p>
              <p style={{ margin: 0, fontSize: 17 }}>{open.prompt}</p>
            </div>
            <dl className="meta">
              <dt>种子</dt><dd className="mono">{open.seed}</dd>
              <dt>工作流</dt><dd>{open.workflow}</dd>
              <dt>算力</dt><dd>{providerById(open.provider)?.name || '示例作品'} · {open.gpu}</dd>
              <dt>耗时</dt><dd className="tabular">{open.seconds.toFixed(1)} s</dd>
              <dt>时间</dt><dd>{fmtTime(open.createdAt)}</dd>
            </dl>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => toCompare('a')}>设为对比 A</button>
              <button className="btn btn-ghost btn-sm" onClick={() => toCompare('b')}>设为对比 B</button>
              <a className="link-btn" style={{ alignSelf: 'center' }} href="#/lab">去对比 ›</a>
            </div>
            <div style={{ marginTop: 'auto', display: 'flex', gap: 8 }}>
              <a className="btn btn-primary btn-sm" href={open.src} download>下载</a>
              <button className="btn btn-danger btn-sm" onClick={() => { removeGalleryItem(open.id); setOpenId(null) }}>删除</button>
              <button className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setOpenId(null)}>关闭</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
