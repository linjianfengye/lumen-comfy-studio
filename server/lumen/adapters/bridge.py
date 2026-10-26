"""浏览器直连的本机 ComfyUI（公开部署用）。

服务器不访问用户填写的 127.0.0.1 / 内网地址：
- 连接时由浏览器读取 /system_stats 和 /object_info 交给后端缓存
- 出图时后端照常把工作流转成 API 格式并注入参数，放进待领取队列
- 打开着的 Lumen 网页轮询领取任务，提交给本机 ComfyUI，再把结果文件传回后端
"""
import asyncio
import time
import uuid

from ..comfy import default_graph, first_checkpoint, inject_params, missing_node_types, model_set, to_api_prompt
from ..workflow import basename
from .base import Adapter, AdapterError

OBJINFO_KEY = "objinfo:local"
OFFLINE_AFTER = 20      # 秒：网页这么久没来领取，视为已关闭


class BridgeAdapter(Adapter):
    engine = None       # Engine 创建适配器时注入

    def _info(self) -> dict:
        info = self.engine.store.get_raw(OBJINFO_KEY)
        if not info:
            raise AdapterError("请到「算力平台」重新连接本机 ComfyUI")
        return info

    async def deploy(self, progress):
        raise AdapterError("本机 ComfyUI 由浏览器直连，请在连接窗口里点「保存并连接」")

    async def start(self):
        if not self.engine.bridge_online():
            raise AdapterError("本机 ComfyUI 需要保持 Lumen 网页打开")

    async def check_models(self, wf, raw):
        info = self._info()
        have = model_set(info)
        missing_models = [m["name"] for m in wf["models"] if m["name"] not in have and basename(m["name"]) not in have]
        missing_nodes = missing_node_types([n["type"] for n in wf["nodes"]], info)
        return {"status": "missing" if missing_models or missing_nodes else "ready",
                "missingModels": missing_models, "missingNodes": missing_nodes}

    async def run_job(self, spec, progress, cancelled):
        info = self._info()
        if spec.raw is None:
            if spec.kind == "video":
                raise AdapterError("生成视频需要先导入视频工作流（如 Wan2.1）")
            ckpt = first_checkpoint(info)
            if not ckpt:
                raise AdapterError("ComfyUI 的 models/checkpoints 里没有模型")
            api = default_graph(ckpt)
        else:
            api = to_api_prompt(spec.raw, info)
        upload = None
        if spec.first_frame:
            upload = {"file": spec.first_frame.name, "name": f"lumen_{spec.first_frame.name}"}
        inject_params(api, prompt=spec.prompt, negative=spec.negative or None, seed=spec.seed, width=spec.width,
                      height=spec.height, frames=spec.frames, fps=spec.fps, image=upload["name"] if upload else None)

        fut = asyncio.get_running_loop().create_future()
        jid = uuid.uuid4().hex
        entry = {"id": jid, "prompt": api, "upload": upload, "claimed": False, "cancel": False,
                 "future": fut, "progress": progress}
        self.engine.bridge[jid] = entry
        try:
            while not fut.done():
                await asyncio.wait({fut}, timeout=1)
                if cancelled():
                    entry["cancel"] = True
                    raise AdapterError("已取消")
                if entry["claimed"] and time.monotonic() - self.engine.bridge_seen > OFFLINE_AFTER * 3:
                    raise AdapterError("Lumen 网页已关闭，本机任务中断")
            return fut.result()
        finally:
            # 已取消的条目留一会儿，让网页在下次轮询时收到取消并中断 ComfyUI
            if entry["cancel"]:
                asyncio.get_running_loop().call_later(OFFLINE_AFTER, self.engine.bridge.pop, jid, None)
            else:
                self.engine.bridge.pop(jid, None)
