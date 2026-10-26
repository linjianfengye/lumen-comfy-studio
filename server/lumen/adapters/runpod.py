"""RunPod Serverless（worker-comfyui）。

Endpoint 用 runpod-workers/worker-comfyui 创建，Min Workers = 0 时无任务即缩容到零。
API：POST /v2/{id}/run  GET /v2/{id}/status/{job}  GET /v2/{id}/health  POST /v2/{id}/cancel/{job}
输入：{"input": {"workflow": <API 格式>, "images": [{"name", "image": base64}]}}
输出：{"images": [{"filename", "type": "base64"|"s3_url", "data"}]}（旧版为 {"message": ...}）
"""
import asyncio
import base64
import time

import httpx

from .. import netguard
from .base import AdapterError, Adapter
from ..comfy import default_graph, inject_params
from ..workflow import is_api

API = "https://api.runpod.ai/v2"


class RunPodAdapter(Adapter):
    def _headers(self):
        key = self.cfg.get("apiKey")
        if not key:
            raise AdapterError("缺少 RunPod API Key")
        return {"Authorization": f"Bearer {key}"}

    def _url(self, path):
        eid = (self.cfg.get("endpointId") or "").strip()
        if not eid:
            raise AdapterError("缺少 Endpoint ID")
        return f"{API}/{eid}{path}"

    async def _req(self, method, path, **kw):
        async with httpx.AsyncClient(timeout=60) as h:
            r = await h.request(method, self._url(path), headers=self._headers(), **kw)
        if r.status_code == 401:
            raise AdapterError("RunPod API Key 无效")
        if r.status_code == 404:
            raise AdapterError("找不到该 Endpoint，请检查 Endpoint ID")
        if r.status_code >= 400:
            raise AdapterError(f"RunPod 返回 {r.status_code}：{r.text[:200]}")
        return r.json()

    async def deploy(self, progress):
        progress(0.4, "检查 Endpoint 健康状态")
        health = await self._req("GET", "/health")
        progress(1, "完成")
        return {"info": {"workers": health.get("workers", {}), "device": self.gpu}}

    async def run_job(self, spec, progress, cancelled):
        if spec.raw is None:
            if spec.kind == "video":
                raise AdapterError("生成视频需要先导入视频工作流")
            # Serverless 没法列出模型，默认图用 worker-comfyui 基础镜像自带的 SDXL
            api = default_graph(self.cfg.get("defaultCkpt") or "sd_xl_base_1.0.safetensors")
        elif is_api(spec.raw):
            import copy
            api = copy.deepcopy(spec.raw)
        else:
            raise AdapterError("RunPod 需要 API 格式的工作流：在 ComfyUI 里用 Export (API) 导出后重新导入")
        images = []
        if spec.first_frame:
            images.append({"name": spec.first_frame.name, "image": base64.b64encode(spec.first_frame.read_bytes()).decode()})
        inject_params(api, prompt=spec.prompt, negative=spec.negative or None, seed=spec.seed, width=spec.width,
                      height=spec.height, frames=spec.frames, fps=spec.fps, image=spec.first_frame.name if spec.first_frame else None)
        body = {"input": {"workflow": api, **({"images": images} if images else {})}}
        job = (await self._req("POST", "/run", json=body))["id"]
        t0 = time.monotonic()
        while True:
            if cancelled():
                await self._req("POST", f"/cancel/{job}")
                raise asyncio.CancelledError()
            st = await self._req("GET", f"/status/{job}")
            s = st.get("status")
            if s == "COMPLETED":
                return await self._output(st.get("output") or {})
            if s in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise AdapterError(f"RunPod 任务{s}：{str(st.get('error') or st.get('output'))[:300]}")
            # IN_QUEUE 包含冷启动时间；进度按等待时间平滑推进
            progress(min(0.9, (time.monotonic() - t0) / (90 if s == "IN_QUEUE" else 60)))
            if time.monotonic() - t0 > 1800:
                raise AdapterError("RunPod 任务超时")
            await asyncio.sleep(2)

    async def _output(self, out):
        imgs = out.get("images") or []
        if not imgs and out.get("message"):
            imgs = [{"filename": "output.png", "type": "s3_url" if str(out["message"]).startswith("http") else "base64", "data": out["message"]}]
        if not imgs:
            raise AdapterError("RunPod 返回结果里没有图片")
        f = imgs[0]
        ext = "." + (f.get("filename", "out.png").rsplit(".", 1)[-1].lower())
        if f.get("type") == "s3_url" or str(f.get("data", "")).startswith("http"):
            # 结果链接来自用户自己的 worker，公开部署时只允许公网地址
            async with httpx.AsyncClient(timeout=300, transport=netguard.transport()) as h:
                data = (await h.get(f["data"])).content
        else:
            data = base64.b64decode(f["data"].split(",", 1)[-1])
        return {"data": data, "ext": ext, "video": ext in (".mp4", ".webm", ".mov")}
