"""本地 / 局域网 ComfyUI：后端直接访问，不需要 CORS。"""
from .base import ComfyAdapter

DEFAULT_URL = "http://127.0.0.1:8188"


class LocalAdapter(ComfyAdapter):
    def comfy_url(self):
        return (self.cfg.get("endpoint") or DEFAULT_URL).strip().rstrip("/")

    async def deploy(self, progress):
        progress(0.3, "探测 /system_stats")
        info = await self.client().probe()
        progress(1, "读取节点列表")
        return {"url": self.comfy_url(), "info": info}

    async def start(self):
        await self.client().system_stats()
