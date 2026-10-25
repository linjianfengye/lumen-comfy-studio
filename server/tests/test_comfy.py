import asyncio
import json

import httpx

from lumen import config as C
from lumen.adapters.base import JobSpec
from lumen.adapters.local import LocalAdapter
from lumen.comfy import ComfyClient, inject_params, model_set, to_api_prompt
from lumen.workflow import parse

INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd_xl_base_1.0.safetensors"]]}}},
    "LoraLoader": {"input": {"required": {"model": ["MODEL"], "clip": ["CLIP"], "lora_name": [["film.safetensors"]],
                                          "strength_model": ["FLOAT", {}], "strength_clip": ["FLOAT", {}]}}},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}], "clip": ["CLIP"]}}},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}], "batch_size": ["INT", {}]}}},
    "KSampler": {"input": {"required": {"model": ["MODEL"], "seed": ["INT", {}], "steps": ["INT", {}], "cfg": ["FLOAT", {}],
                                        "sampler_name": [["euler"]], "scheduler": [["normal"]], "positive": ["CONDITIONING"],
                                        "negative": ["CONDITIONING"], "latent_image": ["LATENT"], "denoise": ["FLOAT", {}]}}},
    "VAEDecode": {"input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}}},
    "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}},
}

# checkpoint → Reroute → 旁路 LoRA → KSampler；Note 要被丢弃
UI = {
    "nodes": [
        {"id": 4, "type": "CheckpointLoaderSimple", "widgets_values": ["sd_xl_base_1.0.safetensors"], "outputs": [{"type": "MODEL"}, {"type": "CLIP"}, {"type": "VAE"}]},
        {"id": 20, "type": "Reroute", "inputs": [{"name": "", "type": "*", "link": 1}], "outputs": [{"type": "MODEL"}]},
        {"id": 10, "type": "LoraLoader", "mode": 4, "inputs": [{"name": "model", "type": "MODEL", "link": 9}, {"name": "clip", "type": "CLIP", "link": 2}],
         "outputs": [{"type": "MODEL"}, {"type": "CLIP"}], "widgets_values": ["film.safetensors", 0.8, 0.8]},
        {"id": 6, "type": "CLIPTextEncode", "inputs": [{"name": "clip", "type": "CLIP", "link": 3}], "widgets_values": ["pos"]},
        {"id": 7, "type": "CLIPTextEncode", "inputs": [{"name": "clip", "type": "CLIP", "link": 4}], "widgets_values": ["neg"]},
        {"id": 5, "type": "EmptyLatentImage", "widgets_values": [1024, 1024, 1]},
        {"id": 3, "type": "KSampler", "inputs": [{"name": "model", "link": 5}, {"name": "positive", "link": 6}, {"name": "negative", "link": 7}, {"name": "latent_image", "link": 8}],
         "widgets_values": [42, "fixed", 20, 7, "euler", "normal", 1]},
        {"id": 8, "type": "VAEDecode", "inputs": [{"name": "samples", "link": 10}, {"name": "vae", "link": 11}]},
        {"id": 9, "type": "SaveImage", "inputs": [{"name": "images", "link": 12}], "widgets_values": ["out"]},
        {"id": 99, "type": "Note", "widgets_values": ["hello"]},
    ],
    "links": [[1, 4, 0, 20, 0, "MODEL"], [9, 20, 0, 10, 0, "MODEL"], [2, 4, 1, 10, 1, "CLIP"], [3, 10, 1, 6, 0, "CLIP"],
              [4, 4, 1, 7, 0, "CLIP"], [5, 10, 0, 3, 0, "MODEL"], [6, 6, 0, 3, 1, "CONDITIONING"], [7, 7, 0, 3, 2, "CONDITIONING"],
              [8, 5, 0, 3, 3, "LATENT"], [10, 3, 0, 8, 0, "LATENT"], [11, 4, 2, 8, 1, "VAE"], [12, 8, 0, 9, 0, "IMAGE"]],
}


def test_ui_to_api_resolves_reroute_and_bypass():
    api = to_api_prompt(UI, INFO)
    assert "20" not in api and "10" not in api and "99" not in api
    assert api["3"]["inputs"]["model"] == ["4", 0]
    assert api["6"]["inputs"]["clip"] == ["4", 1]
    # seed 后的 "fixed" 被跳过，后面的 widgets 对齐
    assert api["3"]["inputs"]["seed"] == 42 and api["3"]["inputs"]["steps"] == 20 and api["3"]["inputs"]["cfg"] == 7


def test_inject_params_traces_sampler_conditioning():
    api = inject_params(to_api_prompt(UI, INFO), prompt="NEW", negative="BAD", seed=5, width=640, height=480)
    assert api["6"]["inputs"]["text"] == "NEW" and api["7"]["inputs"]["text"] == "BAD"
    assert api["3"]["inputs"]["seed"] == 5
    assert (api["5"]["inputs"]["width"], api["5"]["inputs"]["height"]) == (640, 480)


def test_video_params_and_model_set():
    api = inject_params(json.loads(json.dumps(C.SAMPLES["wanVideo"])), prompt="V", frames=65, fps=24)
    assert api["40"]["inputs"]["length"] == 65 and api["28"]["inputs"]["fps"] == 24
    assert "sd_xl_base_1.0.safetensors" in model_set(INFO)


def test_parse_samples():
    sdxl = parse(C.SAMPLES["sdxl"], "a.json")
    assert sdxl["format"] == "ui" and sdxl["kind"] == "image"
    assert {m["name"] for m in sdxl["models"]} >= {"sd_xl_base_1.0.safetensors", "4x-UltraSharp.pth"}
    assert any(c["pack"] == "comfyui-impact-pack" for c in sdxl["customNodes"])
    wan = parse(C.SAMPLES["wanVideo"], "v.json")
    assert wan["format"] == "api" and wan["kind"] == "video"
    assert next(m for m in wan["models"] if m["name"].startswith("wan2.1"))["source"].startswith("https://huggingface.co/")


def fake_comfy():
    """用 httpx.MockTransport 模拟一个 ComfyUI：提交后第二次查询 /history 才完成。"""
    seen = {"prompt": None, "polls": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.42.5"}, "devices": [{"name": "cuda:0 RTX 4090 : x", "vram_total": 24 * 2**30}]})
        if p == "/object_info":
            return httpx.Response(200, json=INFO)
        if p == "/prompt":
            seen["prompt"] = json.loads(req.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "p1"})
        if p == "/history/p1":
            seen["polls"] += 1
            if seen["polls"] < 2:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"p1": {"status": {"status_str": "success", "completed": True},
                                                    "outputs": {"9": {"images": [{"filename": "o.png", "subfolder": "", "type": "output"}]}}}})
        if p == "/view":
            return httpx.Response(200, content=b"PNGDATA")
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def test_local_adapter_end_to_end():
    async def go():
        transport, seen = fake_comfy()
        ad = LocalAdapter("local", {"config": {"endpoint": "http://comfy.test"}, "comfy": {}})
        ad._client = ComfyClient("http://comfy.test", transport=transport)
        ad._client.run.__func__  # noqa: B018 — 确认可调用
        info = await ad.deploy(lambda *a: None)
        assert info["info"]["device"] == "RTX 4090" and info["info"]["vram"] == 24.0

        async def fast_run(api, on_progress=None, cancelled=lambda: False, timeout=60, poll=0.01):
            return await ComfyClient.run(ad._client, api, on_progress, cancelled, timeout, poll)
        ad._client.run = fast_run
        res = await ad.run_job(JobSpec(kind="image", prompt="hello", seed=9, raw=UI), lambda x: None, lambda: False)
        assert res["data"] == b"PNGDATA" and res["ext"] == ".png"
        assert seen["prompt"]["6"]["inputs"]["text"] == "hello" and seen["prompt"]["3"]["inputs"]["seed"] == 9
        await ad.close()

    asyncio.run(go())
