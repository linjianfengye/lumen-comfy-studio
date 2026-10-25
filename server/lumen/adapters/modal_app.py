"""Modal：渲染 modal_template.py → `modal deploy`，得到 ComfyUI 的 web endpoint，之后走 ComfyUI HTTP。

- 空闲关机：部署时把 scaledown_window 设为策略里的空闲分钟数，由 Modal 自动缩容到零
- 模型：`modal run app.py::download_model` 下载到 Modal Volume，ComfyUI 通过 extra_model_paths 读取
- 自定义节点：工作流用到的 cnr_id 写进镜像，新增节点包时自动重新部署
"""
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .. import config as C
from ..comfy import ComfyError
from .base import AdapterError, ComfyAdapter

TEMPLATE = Path(__file__).with_name("modal_template.py")
INSPECT = Path(__file__).with_name("modal_inspect.py")
COMFY_CLI = os.environ.get("LUMEN_COMFY_CLI", "1.23.0")
COMFY_VERSION = os.environ.get("LUMEN_COMFY_VERSION", "0.42.5")
APP_NAME = "lumen-comfyui"
GPU_NAME = {"A100-40G": "A100-40GB", "A100-80G": "A100-80GB"}
URL_RE = re.compile(r"https://[\w.-]+\.modal\.run\S*")
STEP_HINTS = [("Building image", 0.15, "构建 ComfyUI 镜像"), ("Creating objects", 0.6, "创建模型存储卷"),
              ("Created", 0.75, "发布 HTTP 接口"), ("App deployed", 0.85, "健康检查")]


class ModalAdapter(ComfyAdapter):
    def comfy_url(self):
        return (self.comfy.get("url") or self.cfg.get("endpoint") or "").strip().rstrip("/")

    def _env(self) -> dict:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "TERM": "dumb"}
        if self.cfg.get("tokenId") and self.cfg.get("tokenSecret"):
            env.update(MODAL_TOKEN_ID=self.cfg["tokenId"], MODAL_TOKEN_SECRET=self.cfg["tokenSecret"])
        env["HF_TOKEN"] = self.cfg.get("hfToken") or ""
        return env

    def render(self, node_packs=(), idle_minutes=5) -> Path:
        cfg = {
            "appName": APP_NAME, "volumeName": f"{APP_NAME}-models", "gpu": GPU_NAME.get(self.gpu, self.gpu),
            "scaledownWindow": max(60, min(3600, int(idle_minutes * 60))), "nodePacks": sorted(set(node_packs)),
            "comfyCli": COMFY_CLI, "comfyVersion": COMFY_VERSION,
        }
        out = C.DEPLOY / "modal_comfyui.py"
        out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__CONFIG__", json.dumps(cfg)), encoding="utf-8")
        return out

    async def _run(self, args, on_line, timeout=3600) -> tuple[int, str]:
        """在线程里跑 modal CLI，逐行回调（回调被投递回事件循环执行）。"""
        return await self._exec(["-m", "modal", *args], on_line, timeout)

    async def _inspect(self, *args, timeout=120) -> dict:
        """调 modal_inspect.py：只查元数据，不开容器。"""
        got = {}

        def on_line(line):
            if line.startswith("LUMEN_JSON "):
                got["v"] = json.loads(line[len("LUMEN_JSON "):])

        code, tail = await self._exec([str(INSPECT), *args], on_line, timeout)
        if code != 0 or "v" not in got:
            raise AdapterError(f"查询 Modal 失败：{tail[-300:]}")
        return got["v"]

    async def volume_files(self) -> set[str]:
        """存储卷里已有的模型文件（相对路径，如 checkpoints/a.safetensors）。"""
        return set((await self._inspect("volume", f"{APP_NAME}-models"))["files"])

    async def _exec(self, args, on_line, timeout) -> tuple[int, str]:
        if importlib.util.find_spec("modal") is None:
            raise AdapterError("后端环境没有安装 modal：在 server 目录执行 .venv\\Scripts\\pip install modal")
        loop = asyncio.get_running_loop()

        def work():
            p = subprocess.Popen([sys.executable, *args], cwd=C.DEPLOY, env=self._env(),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            tail = []
            for line in p.stdout:
                line = line.rstrip()
                tail = (tail + [line])[-30:]
                loop.call_soon_threadsafe(on_line, line)
            return p.wait(timeout=timeout), "\n".join(tail)

        return await asyncio.to_thread(work)

    def desired(self, node_packs, idle_minutes) -> dict:
        return {"gpu": self.gpu, "nodePacks": sorted(set(node_packs)), "scaledownMinutes": idle_minutes,
                "comfyVersion": COMFY_VERSION}

    @staticmethod
    def covers(have: dict | None, want: dict) -> bool:
        """已部署的配置是否满足需要：节点包只要是超集即可，其余要一致。"""
        if not have:
            return False
        return (set(want["nodePacks"]) <= set(have.get("nodePacks") or [])
                and all(have.get(k) == want[k] for k in ("gpu", "scaledownMinutes", "comfyVersion")))

    async def deploy(self, progress, node_packs=(), idle_minutes=5, verify=False):
        has_token = self.cfg.get("tokenId") and self.cfg.get("tokenSecret")
        if not has_token:
            if not self.cfg.get("endpoint"):
                raise AdapterError("请填写 Modal Token（由 Lumen 部署），或填写已部署的 Web Endpoint")
            self.comfy["url"] = self.cfg["endpoint"].rstrip("/")
            info = {"device": "首次出图时冷启动"}
            if verify:
                progress(0.5, "探测已部署的 ComfyUI（冷启动可能需要 1–2 分钟）")
                info = await self.client().probe(timeout=300)
            progress(1, "完成")
            return {"url": self.comfy["url"], "info": info, "managed": False}

        want = self.desired(node_packs, idle_minutes)
        progress(0.03, "检查 Modal 上已有的部署")
        try:
            have = await self._inspect("app", APP_NAME)
        except AdapterError:
            have = {"url": None}
        if have.get("url") and self.covers(have.get("config"), want):
            self.comfy["url"] = have["url"].rstrip("/")
            progress(1, "沿用已有部署")
            return {"url": self.comfy["url"], "info": {"device": "沿用已有部署 · 首次出图时冷启动"}, "managed": True,
                    "reused": True, **have["config"]}
        # 已部署的节点包要保留，避免来回重装
        want["nodePacks"] = sorted(set(want["nodePacks"]) | set((have.get("config") or {}).get("nodePacks") or []))
        node_packs = want["nodePacks"]

        path = self.render(node_packs, idle_minutes)
        found = {}

        def on_line(line):
            for key, p, label in STEP_HINTS:
                if key in line:
                    progress(p, label)
            m = URL_RE.search(line)
            if m and "ui" in m.group(0):
                found["url"] = m.group(0).rstrip("/").rstrip(".")

        progress(0.05, "构建 ComfyUI 镜像（首次约 5–10 分钟）")
        code, tail = await self._run(["deploy", path.name], on_line)
        if code != 0:
            raise AdapterError(f"modal deploy 失败：{tail[-500:]}")
        url = found.get("url")
        if not url:
            raise AdapterError(f"部署成功但没有解析到 Web Endpoint 地址，请在 Modal 控制台复制后填入：{tail[-300:]}")
        self.comfy["url"] = url
        try:
            await self._inspect("save", APP_NAME, json.dumps(want))
        except AdapterError:
            pass  # 只影响下次能否沿用，不影响本次部署
        info = {"device": "首次出图时冷启动"}
        if verify:
            progress(0.9, "健康检查（会冷启动一次 GPU，约 1–2 分钟）")
            info = await self.client().probe(timeout=300)
        progress(1, "完成")
        return {"url": url, "info": info, "managed": True, **want}

    async def start(self):
        # 第一次请求触发 Modal 冷启动
        try:
            await self.client().system_stats(timeout=300)
        except ComfyError as e:
            raise AdapterError(f"Modal 冷启动失败：{e}")

    async def stop(self):
        """不做操作：Modal 在 scaledown_window 到期后自动缩容到零。"""

    def needs_redeploy(self, node_packs, idle_minutes) -> bool:
        if not self.comfy.get("managed"):
            return False
        return not self.covers({**self.comfy, "comfyVersion": self.comfy.get("comfyVersion", COMFY_VERSION)},
                               self.desired(node_packs, idle_minutes))

    async def deploy_model(self, model, progress):
        if not (self.cfg.get("tokenId") and self.cfg.get("tokenSecret")):
            raise AdapterError("未填写 Modal Token，无法向存储卷下载模型；请把模型放进你自己部署的 ComfyUI")
        if not model.get("source"):
            raise AdapterError("没有下载地址，请在工作流页为该模型填写下载链接")
        path = C.DEPLOY / "modal_comfyui.py"
        if not path.exists():
            self.render(self.comfy.get("nodePacks") or [], self.comfy.get("scaledownMinutes") or 5)

        def on_line(line):
            if line.startswith("LUMEN_PROGRESS"):
                progress(float(line.split()[1]))

        filename = model["name"].replace("\\", "/").split("/")[-1]
        code, tail = await self._run(["run", f"{path.name}::download_model", "--url", model["source"],
                                      "--subdir", model["dir"], "--filename", filename], on_line)
        if code != 0:
            raise AdapterError(f"模型下载失败：{tail[-400:]}")
