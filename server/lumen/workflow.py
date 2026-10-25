"""解析 ComfyUI 工作流（移植自前端 src/lib/workflow.js，两边规则保持一致）。

UI 格式（界面"保存"）：{ nodes: [{ id, type, widgets_values, properties }], links }
API 格式（"导出 API"）：{ "3": { class_type, inputs } }
"""
import re
import time
import uuid

MODEL_EXT = re.compile(r"\.(safetensors|ckpt|pt|pth|bin|gguf|sft|onnx)$", re.I)

LOADER_DIR = {
    "CheckpointLoaderSimple": "checkpoints", "CheckpointLoader": "checkpoints",
    "ImageOnlyCheckpointLoader": "checkpoints", "unCLIPCheckpointLoader": "checkpoints",
    "LoraLoader": "loras", "LoraLoaderModelOnly": "loras", "VAELoader": "vae",
    "ControlNetLoader": "controlnet", "DiffControlNetLoader": "controlnet",
    "UNETLoader": "diffusion_models", "UnetLoaderGGUF": "diffusion_models",
    "CLIPLoader": "text_encoders", "DualCLIPLoader": "text_encoders", "TripleCLIPLoader": "text_encoders",
    "CLIPVisionLoader": "clip_vision", "UpscaleModelLoader": "upscale_models",
    "StyleModelLoader": "style_models", "GLIGENLoader": "gligen", "PhotoMakerLoader": "photomaker",
}

KEY_DIR = {
    "ckpt_name": "checkpoints", "lora_name": "loras", "vae_name": "vae", "control_net_name": "controlnet",
    "unet_name": "diffusion_models", "clip_name": "text_encoders", "clip_name1": "text_encoders",
    "clip_name2": "text_encoders", "clip_name3": "text_encoders", "model_name": "upscale_models",
    "style_model_name": "style_models",
}

CORE_NODES = set(LOADER_DIR) | {
    "KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced", "CLIPTextEncode",
    "CLIPTextEncodeSDXL", "CLIPTextEncodeFlux", "EmptyLatentImage", "EmptySD3LatentImage",
    "VAEDecode", "VAEEncode", "VAEDecodeTiled", "SaveImage", "PreviewImage", "LoadImage",
    "ImageScale", "ImageUpscaleWithModel", "LatentUpscale", "ConditioningCombine",
    "ControlNetApply", "ControlNetApplyAdvanced", "FluxGuidance", "BasicGuider", "CFGGuider",
    "BasicScheduler", "KSamplerSelect", "RandomNoise", "ModelSamplingFlux", "ModelSamplingSD3",
    "Note", "MarkdownNote", "Reroute", "PrimitiveNode", "SaveAnimatedWEBP", "SaveAnimatedPNG",
    "SaveWEBM", "SaveVideo", "CreateVideo", "WanImageToVideo", "EmptyHunyuanLatentVideo",
    "EmptyLTXVLatentVideo", "LTXVConditioning", "EmptyMochiLatentVideo", "CLIPVisionEncode",
}

VIDEO_HINT = re.compile(r"video|animat|webm|vhs_|wan|hunyuan|ltxv|mochi|svd|frame", re.I)

# 常见模型的体积（GB）与下载地址。url 是可直接下载的文件地址，供云端部署使用
HF = "https://huggingface.co"
KNOWN_MODELS = {
    "sd_xl_base_1.0.safetensors": {"size": 6.94, "url": f"{HF}/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"},
    "sd_xl_refiner_1.0.safetensors": {"size": 6.08, "url": f"{HF}/stabilityai/stable-diffusion-xl-refiner-1.0/resolve/main/sd_xl_refiner_1.0.safetensors"},
    "v1-5-pruned-emaonly.safetensors": {"size": 4.27, "url": f"{HF}/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly.safetensors"},
    "flux1-dev-fp8.safetensors": {"size": 17.2, "url": f"{HF}/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors"},
    "flux1-schnell.safetensors": {"size": 23.8, "url": f"{HF}/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors"},
    "ae.safetensors": {"size": 0.33},
    "clip_l.safetensors": {"size": 0.25, "url": f"{HF}/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors"},
    "t5xxl_fp8_e4m3fn.safetensors": {"size": 4.89, "url": f"{HF}/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors"},
    "t5xxl_fp16.safetensors": {"size": 9.79, "url": f"{HF}/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors"},
    "sdxl_vae.safetensors": {"size": 0.33, "url": f"{HF}/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors"},
    "RealESRGAN_x4plus.pth": {"size": 0.07},
    "4x-UltraSharp.pth": {"size": 0.07},
    "wan2.1_t2v_1.3B_fp16.safetensors": {"size": 2.84, "url": f"{HF}/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors"},
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors": {"size": 6.74, "url": f"{HF}/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"},
    "wan_2.1_vae.safetensors": {"size": 0.25, "url": f"{HF}/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors"},
}


class WorkflowError(ValueError):
    pass


def is_ui(j) -> bool:
    return isinstance(j, dict) and isinstance(j.get("nodes"), list)


def is_api(j) -> bool:
    if not isinstance(j, dict) or not j:
        return False
    return all(isinstance(v, dict) and "class_type" in v for v in j.values())


def _normalize(j):
    if is_api(j):
        return [{
            "id": str(k), "type": n["class_type"], "title": (n.get("_meta") or {}).get("title") or n["class_type"],
            "inputs": {a: b for a, b in (n.get("inputs") or {}).items() if not isinstance(b, list)},
            "widgets": [], "meta": {},
        } for k, n in j.items()]
    return [{
        "id": str(n.get("id")), "type": n.get("type"), "title": n.get("title") or n.get("type"),
        "inputs": {}, "widgets": n.get("widgets_values") if isinstance(n.get("widgets_values"), list) else [],
        "meta": n.get("properties") or {},
    } for n in j["nodes"]]


def basename(name: str) -> str:
    return re.split(r"[\\/]", name)[-1]


def parse(j, file_name: str = "workflow.json") -> dict:
    if not is_ui(j) and not is_api(j):
        raise WorkflowError("无法识别：请导出 ComfyUI 的工作流 JSON（UI 格式或 API 格式）")
    nodes = _normalize(j)
    models, packs, prompts = {}, {}, []
    for n in nodes:
        values = list(n["inputs"].items()) + [(f"w{i}", v) for i, v in enumerate(n["widgets"])]
        for key, value in values:
            if isinstance(value, str) and MODEL_EXT.search(value) and value not in models:
                known = KNOWN_MODELS.get(basename(value), {})
                models[value] = {
                    "name": value, "dir": LOADER_DIR.get(n["type"]) or KEY_DIR.get(key) or "checkpoints",
                    "size": known.get("size"), "source": known.get("url"),
                }
        if "CLIPTextEncode" in (n["type"] or ""):
            text = n["inputs"].get("text")
            if text is None:
                text = next((w for w in n["widgets"] if isinstance(w, str)), None)
            if isinstance(text, str):
                prompts.append({"nodeId": n["id"], "title": n["title"], "text": text})
        if n["type"] not in CORE_NODES and n["meta"].get("cnr_id") != "comfy-core":
            pack = n["meta"].get("cnr_id") or n["meta"].get("aux_id") or "未知来源"
            packs.setdefault(pack, set()).add(n["type"])
    return {
        "id": str(uuid.uuid4()),
        "name": re.sub(r"\.json$", "", file_name, flags=re.I),
        "format": "api" if is_api(j) else "ui",
        "kind": "video" if any(VIDEO_HINT.search(n["type"] or "") for n in nodes) else "image",
        "nodes": [{"id": n["id"], "type": n["type"], "title": n["title"]} for n in nodes],
        "models": list(models.values()),
        "customNodes": [{"pack": k, "types": sorted(v)} for k, v in packs.items()],
        "prompts": prompts,
        "targets": {},     # providerId -> {status, current, progress, error}
        "checks": {},      # providerId -> {status, missingModels, missingNodes}（check 模式的平台）
        "createdAt": int(time.time() * 1000),
    }


def total_size(models) -> float:
    return sum(m.get("size") or 0 for m in models)
