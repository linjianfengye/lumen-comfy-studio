"""Vast.ai：通过 REST API 开关实例，任务直接提交到实例上的 ComfyUI。
API：PUT https://console.vast.ai/api/v0/instances/{id}/  {"state": "running" | "stopped"}
"""
import asyncio
import time

import httpx

from .base import AdapterError, ComfyAdapter
from ..comfy import ComfyError

API = "https://console.vast.ai/api/v0"


class VastAdapter(ComfyAdapter):
    def comfy_url(self):
        return (self.cfg.get("uiUrl") or "").strip().rstrip("/")

    def comfy_headers(self):
        # 新版 Vast ComfyUI 模板用 Caddy 做鉴权，令牌放在 Bearer 头里
        t = self.cfg.get("comfyToken")
        return {"Authorization": f"Bearer {t}"} if t else {}

    async def _api(self, method, path, **kw):
        key = self.cfg.get("apiKey")
        if not key:
            raise AdapterError("缺少 Vast API Key")
        async with httpx.AsyncClient(timeout=30) as h:
            r = await h.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {key}"}, **kw)
        if r.status_code == 401:
            raise AdapterError("Vast API Key 无效")
        if r.status_code >= 400:
            raise AdapterError(f"Vast API 返回 {r.status_code}：{r.text[:200]}")
        return r.json() if r.content else {}

    async def _instance(self):
        j = await self._api("GET", f"/instances/{self.cfg['instanceId']}/")
        inst = j.get("instances", j)
        return inst[0] if isinstance(inst, list) and inst else inst

    async def deploy(self, progress):
        progress(0.2, "校验 API Key 与实例")
        inst = await self._instance()
        status = inst.get("actual_status") or inst.get("cur_state") or "unknown"
        info = {"device": inst.get("gpu_name") or self.gpu, "status": status}
        if status == "running" and self.comfy_url():
            progress(0.6, "探测 ComfyUI")
            try:
                info.update(await self.client().probe(timeout=15))
            except ComfyError as e:
                info["warning"] = str(e)
        progress(1, "完成")
        return {"url": self.comfy_url(), "info": info}

    async def start(self):
        await self._api("PUT", f"/instances/{self.cfg['instanceId']}/", json={"state": "running"})
        # 等 ComfyUI 起来
        t0 = time.monotonic()
        while time.monotonic() - t0 < 600:
            try:
                await self.client().system_stats(timeout=8)
                return
            except ComfyError:
                await asyncio.sleep(5)
        raise AdapterError("实例已开机，但 10 分钟内 ComfyUI 没有响应")

    async def stop(self):
        await self._api("PUT", f"/instances/{self.cfg['instanceId']}/", json={"state": "stopped"})

    async def is_running(self) -> bool:
        inst = await self._instance()
        return (inst.get("actual_status") or "") == "running"
