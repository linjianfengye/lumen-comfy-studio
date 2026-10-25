"""适配器接口与基于 ComfyUI HTTP 的通用实现（本地、Vast、Modal 共用）。"""
from dataclasses import dataclass
from pathlib import Path

from .. import config as C
from ..comfy import (ComfyClient, ComfyError, default_graph, first_checkpoint, inject_params,
                     missing_node_types, model_set, to_api_prompt)
from ..workflow import basename


class AdapterError(RuntimeError):
    pass


@dataclass
class JobSpec:
    kind: str                     # image | video
    prompt: str
    seed: int
    raw: dict | None = None       # 原始工作流 JSON；None 用默认文生图
    negative: str = ""
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    fps: int | None = None
    first_frame: Path | None = None


class Adapter:
    """生命周期：deploy（部署 ComfyUI）→ start（开机/预热）→ run_job → stop（关机）。"""

    def __init__(self, pid: str, provider: dict):
        self.pid = pid
        self.meta = C.PROVIDER_BY_ID[pid]
        self.cfg = dict(provider.get("config") or {})
        self.comfy = dict(provider.get("comfy") or {})
        self.gpu = provider.get("gpu")

    @property
    def lifecycle(self) -> str:
        return self.meta["lifecycle"]

    async def deploy(self, progress) -> dict:
        """部署或探测 ComfyUI。progress(0..1, 步骤名)。返回写入 provider.comfy 的字段（url、info…）。"""
        raise NotImplementedError

    async def start(self):
        """开机 / 预热。返回时应能接任务。"""

    async def stop(self):
        """关机。Serverless 平台由平台自行缩容，这里为空。"""

    async def deploy_model(self, model: dict, progress):
        raise AdapterError("该平台不支持由 Lumen 下载模型")

    async def check_models(self, wf: dict, raw: dict | None) -> dict:
        raise AdapterError("该平台不支持核对模型")

    async def run_job(self, spec: JobSpec, progress, cancelled) -> dict:
        """返回 {data: bytes, ext, video} 或演示用的 {src}。"""
        raise NotImplementedError

    async def close(self):
        pass


class ComfyAdapter(Adapter):
    """直接访问 ComfyUI HTTP 接口的平台。"""

    def comfy_url(self) -> str:
        return (self.comfy.get("url") or self.cfg.get("endpoint") or "").strip().rstrip("/")

    def comfy_headers(self) -> dict:
        return {}

    _client: ComfyClient | None = None

    def client(self) -> ComfyClient:
        url = self.comfy_url()
        if not url:
            raise AdapterError("还没有 ComfyUI 地址，请先部署或填写地址")
        if not self._client or self._client.base != url:
            self._client = ComfyClient(url, self.comfy_headers())
        return self._client

    async def check_models(self, wf, raw):
        info = await self.client().object_info(force=True)
        have = model_set(info)
        missing_models = [m["name"] for m in wf["models"] if m["name"] not in have and basename(m["name"]) not in have]
        missing_nodes = missing_node_types([n["type"] for n in wf["nodes"]], info)
        return {"status": "missing" if missing_models or missing_nodes else "ready",
                "missingModels": missing_models, "missingNodes": missing_nodes}

    async def run_job(self, spec, progress, cancelled):
        c = self.client()
        info = await c.object_info()
        if spec.raw is None:
            if spec.kind == "video":
                raise AdapterError("生成视频需要先导入视频工作流（如 Wan2.1）")
            ckpt = first_checkpoint(info)
            if not ckpt:
                raise AdapterError("ComfyUI 的 models/checkpoints 里没有模型")
            api = default_graph(ckpt)
        else:
            api = to_api_prompt(spec.raw, info)
        image = await c.upload_image(spec.first_frame.name, spec.first_frame.read_bytes()) if spec.first_frame else None
        inject_params(api, prompt=spec.prompt, negative=spec.negative or None, seed=spec.seed, width=spec.width,
                      height=spec.height, frames=spec.frames, fps=spec.fps, image=image)
        files = await c.run(api, on_progress=progress, cancelled=cancelled)
        if not files:
            raise AdapterError("工作流没有输出文件（缺少 SaveImage / VHS_VideoCombine 等保存节点）")
        f = next((x for x in files if x["video"]), files[0])
        return {"data": await c.download(f), "ext": Path(f["filename"]).suffix.lower() or ".png", "video": f["video"]}

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None


__all__ = ["Adapter", "ComfyAdapter", "JobSpec", "AdapterError", "ComfyError"]
