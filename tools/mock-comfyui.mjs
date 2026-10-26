// 本地联调用的 ComfyUI 模拟服务（只用 Node 内置模块）：实现 Lumen 用到的接口，出图返回示例图。
// 用法：node tools/mock-comfyui.mjs  （默认端口 8199，MOCK_PORT 可改）
// 然后：COMFY_URL=http://127.0.0.1:8199 npm run dev
// MOCK_CORS=<网站地址> 模拟 ComfyUI 的 --enable-cors-header，用于测试在线版的浏览器直连
// 不实现 WebSocket，客户端会自动退回轮询 /history。
import http from 'node:http'
import { readFile } from 'node:fs/promises'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const PORT = Number(process.env.MOCK_PORT || 8199)
const SAMPLES = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'samples')
const OUTPUTS = ['lake.webp', 'city.webp', 'tea.webp', 'dune.webp', 'perfume.webp', 'portrait.webp']
const CORS = process.env.MOCK_CORS || ''
const RUN_MS = 3000

const combo = (list) => [list]
const OBJECT_INFO = {
  CheckpointLoaderSimple: { input: { required: { ckpt_name: combo(['sd_xl_base_1.0.safetensors', 'juggernautXL_v9.safetensors']) } } },
  LoraLoader: { input: { required: { model: ['MODEL'], clip: ['CLIP'], lora_name: combo(['film_grain_sdxl.safetensors']), strength_model: ['FLOAT', {}], strength_clip: ['FLOAT', {}] } } },
  CLIPTextEncode: { input: { required: { text: ['STRING', { multiline: true }], clip: ['CLIP'] } } },
  EmptyLatentImage: { input: { required: { width: ['INT', {}], height: ['INT', {}], batch_size: ['INT', {}] } } },
  KSampler: { input: { required: { model: ['MODEL'], seed: ['INT', { control_after_generate: true }], steps: ['INT', {}], cfg: ['FLOAT', {}], sampler_name: combo(['euler', 'dpmpp_2m']), scheduler: combo(['normal', 'karras']), positive: ['CONDITIONING'], negative: ['CONDITIONING'], latent_image: ['LATENT'], denoise: ['FLOAT', {}] } } },
  VAEDecode: { input: { required: { samples: ['LATENT'], vae: ['VAE'] } } },
  SaveImage: { input: { required: { images: ['IMAGE'], filename_prefix: ['STRING', {}] } } },
  LoadImage: { input: { required: { image: combo(['example.png']) } } },
  UpscaleModelLoader: { input: { required: { model_name: combo(['RealESRGAN_x4plus.pth']) } } },
}

const jobs = new Map()
let counter = 0

const send = (res, code, body, type = 'application/json') => {
  res.writeHead(code, { 'Content-Type': type, ...(CORS ? { 'Access-Control-Allow-Origin': CORS } : {}) })
  res.end(type === 'application/json' ? JSON.stringify(body) : body)
}
const readBody = (req) => new Promise((ok) => { const c = []; req.on('data', (d) => c.push(d)); req.on('end', () => ok(Buffer.concat(c))) })

http.createServer(async (req, res) => {
  // 模拟新版 ComfyUI：本机请求的 Origin 与 Host 不一致时拒绝（验证 Vite 代理是否去掉了 Origin）
  const origin = req.headers.origin
  if (CORS && req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': CORS, 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' })
    return res.end()
  }
  if (origin && origin !== CORS && new URL(origin).host !== req.headers.host) return send(res, 403, { error: 'origin mismatch' })
  const url = new URL(req.url, `http://${req.headers.host}`)
  const path = url.pathname

  if (path === '/system_stats') {
    return send(res, 200, { system: { comfyui_version: '0.3.40-mock' }, devices: [{ name: 'cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync', vram_total: 24 * 2 ** 30 }] })
  }
  if (path === '/object_info') return send(res, 200, OBJECT_INFO)
  if (path === '/prompt' && req.method === 'POST') {
    const body = JSON.parse((await readBody(req)).toString() || '{}')
    const errors = Object.values(body.prompt || {}).filter((n) => !OBJECT_INFO[n.class_type]).map((n) => n.class_type)
    if (errors.length) return send(res, 400, { error: { message: 'Prompt outputs failed validation' }, node_errors: { 1: { class_type: errors[0], errors: [{ message: 'unknown node' }] } } })
    const id = `mock-${++counter}`
    jobs.set(id, { at: Date.now(), out: OUTPUTS[counter % OUTPUTS.length], prompt: body.prompt })
    console.log('prompt', id, JSON.stringify(Object.values(body.prompt).find((n) => n.class_type === 'CLIPTextEncode')?.inputs.text))
    return send(res, 200, { prompt_id: id, number: counter, node_errors: {} })
  }
  if (path.startsWith('/history/')) {
    const id = path.split('/').pop()
    const j = jobs.get(id)
    if (!j || Date.now() - j.at < RUN_MS) return send(res, 200, {})
    return send(res, 200, { [id]: { status: { status_str: 'success', completed: true }, outputs: { 9: { images: [{ filename: j.out, subfolder: '', type: 'output' }] } } } })
  }
  if (path === '/view') {
    try { return send(res, 200, await readFile(join(SAMPLES, url.searchParams.get('filename').replace(/[\\/]/g, ''))), 'image/webp') } catch { return send(res, 404, { error: 'not found' }) }
  }
  if (path === '/upload/image' && req.method === 'POST') { await readBody(req); return send(res, 200, { name: 'lumen_upload.png', subfolder: '', type: 'input' }) }
  if (path === '/interrupt') return send(res, 200, {})
  send(res, 404, { error: 'not found' })
}).listen(PORT, '127.0.0.1', () => console.log(`mock ComfyUI on http://127.0.0.1:${PORT}`))
