// 解析 ComfyUI 工作流，支持两种格式：
//   UI 格式（界面"保存"导出）：{ nodes: [{ id, type, widgets_values, properties }], links }
//   API 格式（"导出 API"）：{ "3": { class_type, inputs } }
// 输出：节点列表、所需模型（含目标目录）、自定义节点、可批量替换的参数、输出类型。

const MODEL_EXT = /\.(safetensors|ckpt|pt|pth|bin|gguf|sft|onnx)$/i

// 加载器节点 → 模型存放目录（ComfyUI/models/<dir>）
const LOADER_DIR = {
  CheckpointLoaderSimple: 'checkpoints',
  CheckpointLoader: 'checkpoints',
  ImageOnlyCheckpointLoader: 'checkpoints',
  unCLIPCheckpointLoader: 'checkpoints',
  LoraLoader: 'loras',
  LoraLoaderModelOnly: 'loras',
  VAELoader: 'vae',
  ControlNetLoader: 'controlnet',
  DiffControlNetLoader: 'controlnet',
  UNETLoader: 'diffusion_models',
  UnetLoaderGGUF: 'diffusion_models',
  CLIPLoader: 'text_encoders',
  DualCLIPLoader: 'text_encoders',
  TripleCLIPLoader: 'text_encoders',
  CLIPVisionLoader: 'clip_vision',
  UpscaleModelLoader: 'upscale_models',
  StyleModelLoader: 'style_models',
  GLIGENLoader: 'gligen',
  PhotoMakerLoader: 'photomaker',
}

// 按输入名推断目录（API 格式或未知加载器时兜底）
const KEY_DIR = {
  ckpt_name: 'checkpoints', lora_name: 'loras', vae_name: 'vae', control_net_name: 'controlnet',
  unet_name: 'diffusion_models', clip_name: 'text_encoders', clip_name1: 'text_encoders',
  clip_name2: 'text_encoders', clip_name3: 'text_encoders', model_name: 'upscale_models',
  style_model_name: 'style_models',
}

// 常见核心节点。不在此表、且没有 cnr_id=comfy-core 标记的节点视为自定义节点。
const CORE_NODES = new Set([
  ...Object.keys(LOADER_DIR),
  'KSampler', 'KSamplerAdvanced', 'SamplerCustom', 'SamplerCustomAdvanced', 'CLIPTextEncode',
  'CLIPTextEncodeSDXL', 'CLIPTextEncodeFlux', 'EmptyLatentImage', 'EmptySD3LatentImage',
  'VAEDecode', 'VAEEncode', 'VAEDecodeTiled', 'SaveImage', 'PreviewImage', 'LoadImage',
  'ImageScale', 'ImageUpscaleWithModel', 'LatentUpscale', 'ConditioningCombine',
  'ControlNetApply', 'ControlNetApplyAdvanced', 'FluxGuidance', 'BasicGuider', 'CFGGuider',
  'BasicScheduler', 'KSamplerSelect', 'RandomNoise', 'ModelSamplingFlux', 'ModelSamplingSD3',
  'Note', 'MarkdownNote', 'Reroute', 'PrimitiveNode', 'SaveAnimatedWEBP', 'SaveAnimatedPNG',
  'SaveWEBM', 'SaveVideo', 'CreateVideo', 'WanImageToVideo', 'EmptyHunyuanLatentVideo',
  'EmptyLTXVLatentVideo', 'LTXVConditioning', 'EmptyMochiLatentVideo', 'CLIPVisionEncode',
])

const VIDEO_HINT = /video|animat|webm|vhs_|wan|hunyuan|ltxv|mochi|svd|frame/i

// 常见模型的参考体积与下载源，用于部署前的预估
const KNOWN_MODELS = {
  'sd_xl_base_1.0.safetensors': { size: 6.94, url: 'https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0' },
  'sd_xl_refiner_1.0.safetensors': { size: 6.08, url: 'https://huggingface.co/stabilityai/stable-diffusion-xl-refiner-1.0' },
  'v1-5-pruned-emaonly.safetensors': { size: 4.27, url: 'https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive' },
  'flux1-dev.safetensors': { size: 23.8, url: 'https://huggingface.co/black-forest-labs/FLUX.1-dev' },
  'flux1-dev-fp8.safetensors': { size: 17.2, url: 'https://huggingface.co/Comfy-Org/flux1-dev' },
  'flux1-schnell.safetensors': { size: 23.8, url: 'https://huggingface.co/black-forest-labs/FLUX.1-schnell' },
  'ae.safetensors': { size: 0.33 },
  'clip_l.safetensors': { size: 0.25 },
  't5xxl_fp8_e4m3fn.safetensors': { size: 4.89 },
  't5xxl_fp16.safetensors': { size: 9.79 },
  'sdxl_vae.safetensors': { size: 0.33 },
  'RealESRGAN_x4plus.pth': { size: 0.07 },
  '4x-UltraSharp.pth': { size: 0.07 },
}

function isUiFormat(json) {
  return json && Array.isArray(json.nodes)
}

function isApiFormat(json) {
  if (!json || typeof json !== 'object' || Array.isArray(json)) return false
  const values = Object.values(json)
  return values.length > 0 && values.every((v) => v && typeof v === 'object' && 'class_type' in v)
}

// 统一成 [{ id, type, title, inputs: {name: value}, widgets: [], meta }]
function normalize(json) {
  if (isApiFormat(json)) {
    return Object.entries(json).map(([id, n]) => ({
      id,
      type: n.class_type,
      title: n._meta?.title || n.class_type,
      inputs: Object.fromEntries(Object.entries(n.inputs || {}).filter(([, v]) => !Array.isArray(v))),
      widgets: [],
      meta: {},
    }))
  }
  return json.nodes.map((n) => ({
    id: String(n.id),
    type: n.type,
    title: n.title || n.type,
    inputs: {},
    widgets: Array.isArray(n.widgets_values) ? n.widgets_values : [],
    meta: n.properties || {},
  }))
}

function modelDir(node, key) {
  return LOADER_DIR[node.type] || KEY_DIR[key] || 'checkpoints'
}

export function parseWorkflow(json, fileName = 'workflow.json') {
  if (!isUiFormat(json) && !isApiFormat(json)) {
    throw new Error('无法识别：请导出 ComfyUI 的工作流 JSON（UI 格式或 API 格式）')
  }
  const format = isApiFormat(json) ? 'api' : 'ui'
  const nodes = normalize(json)
  const models = new Map()
  const customNodes = new Map()
  const prompts = []

  for (const node of nodes) {
    const values = [...Object.entries(node.inputs), ...node.widgets.map((v, i) => [`w${i}`, v])]
    for (const [key, value] of values) {
      if (typeof value === 'string' && MODEL_EXT.test(value) && !models.has(value)) {
        const known = KNOWN_MODELS[value.split(/[\\/]/).pop()]
        models.set(value, {
          name: value,
          dir: modelDir(node, key),
          size: known?.size ?? null,
          source: known?.url ?? null,
          status: 'pending',
          progress: 0,
        })
      }
    }
    if (/CLIPTextEncode/.test(node.type)) {
      const text = node.inputs.text ?? node.widgets.find((w) => typeof w === 'string')
      if (typeof text === 'string') prompts.push({ nodeId: node.id, title: node.title, text })
    }
    const isCore = CORE_NODES.has(node.type) || node.meta.cnr_id === 'comfy-core'
    if (!isCore) {
      const pack = node.meta.cnr_id || node.meta.aux_id || '未知来源'
      if (!customNodes.has(pack)) customNodes.set(pack, { pack, types: new Set(), status: 'pending' })
      customNodes.get(pack).types.add(node.type)
    }
  }

  const kind = nodes.some((n) => VIDEO_HINT.test(n.type)) ? 'video' : 'image'
  return {
    id: crypto.randomUUID(),
    name: fileName.replace(/\.json$/i, ''),
    format,
    kind,
    nodes: nodes.map(({ id, type, title }) => ({ id, type, title })),
    models: [...models.values()],
    customNodes: [...customNodes.values()].map((c) => ({ ...c, types: [...c.types] })),
    prompts,
    deploy: 'idle',
    createdAt: Date.now(),
  }
}

export const totalSize = (models) =>
  models.reduce((sum, m) => sum + (m.size || 0), 0)
