"""ComfyUI HTTP 客户端（后端版）。本地、Vast、Modal 部署的 ComfyUI 都走这里。

接口：/system_stats /object_info /prompt /history/{id} /view /upload/image /interrupt
"""
import asyncio
import copy
import re
import time
import uuid

import httpx

NON_EXEC = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode"}
CONTROL_WORDS = {"fixed", "increment", "decrement", "randomize"}
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
VIDEO_EXT = re.compile(r"\.(mp4|webm|mov|mkv)$", re.I)


class ComfyError(RuntimeError):
    pass


def _err_text(res: httpx.Response) -> str:
    msg = f"HTTP {res.status_code}"
    try:
        j = res.json()
        msg = (j.get("error") or {}).get("message") or j.get("message") or msg
        errs = [f"{n.get('class_type')}: {e.get('details') or e.get('message')}"
                for n in (j.get("node_errors") or {}).values() for e in n.get("errors", [])]
        if errs:
            msg += "（" + "；".join(errs[:3]) + "）"
    except Exception:
        pass
    return msg


class ComfyClient:
    def __init__(self, base: str, headers: dict | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.base = base.rstrip("/")
        self.http = httpx.AsyncClient(base_url=self.base, headers=headers or {}, timeout=30, transport=transport, follow_redirects=True)
        self._info = None

    async def close(self):
        await self.http.aclose()

    async def _get(self, path, timeout=30, **kw):
        try:
            r = await self.http.get(path, timeout=timeout, **kw)
        except httpx.HTTPError as e:
            raise ComfyError(f"连不上 ComfyUI（{self.base}）：{e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise ComfyError(_err_text(r))
        return r

    async def system_stats(self, timeout=8):
        return (await self._get("/system_stats", timeout=timeout)).json()

    async def object_info(self, force=False):
        if self._info is None or force:
            self._info = (await self._get("/object_info", timeout=60)).json()
        return self._info

    async def probe(self, timeout=8) -> dict:
        stats = await self.system_stats(timeout)
        info = await self.object_info(force=True)
        dev = (stats.get("devices") or [None])[0]
        return {
            "version": (stats.get("system") or {}).get("comfyui_version", ""),
            "device": re.sub(r"^cuda:\d+\s*", "", dev["name"]).split(" : ")[0].strip() if dev else "CPU",
            "vram": round(dev["vram_total"] / 2**30, 1) if dev else 0,
            "nodeCount": len(info),
            "checkpoints": len(choices_of(spec_of(info.get("CheckpointLoaderSimple"), "ckpt_name"))),
        }

    async def submit(self, api: dict, client_id: str) -> str:
        try:
            r = await self.http.post("/prompt", json={"prompt": api, "client_id": client_id}, timeout=60)
        except httpx.HTTPError as e:
            raise ComfyError(f"提交失败：{e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise ComfyError(_err_text(r))
        return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        return (await self._get(f"/history/{prompt_id}")).json().get(prompt_id)

    async def run(self, api: dict, on_progress=None, cancelled=lambda: False, timeout=1800, poll=1.5) -> list[dict]:
        """提交并轮询到完成，返回输出文件列表 [{filename, subfolder, type, video}]。"""
        pid = await self.submit(api, str(uuid.uuid4()))
        t0 = time.monotonic()
        while True:
            if cancelled():
                await self.interrupt()
                raise asyncio.CancelledError()
            if time.monotonic() - t0 > timeout:
                raise ComfyError("等待超时")
            entry = await self.history(pid)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    err = next((m[1] for m in status.get("messages", []) if m[0] == "execution_error"), None)
                    raise ComfyError(f"{err['node_type']}: {err['exception_message']}" if err else "ComfyUI 执行出错")
                if status.get("completed", True):
                    return collect_outputs(entry)
            elif on_progress:
                # 没有 WebSocket 时按已等待时间给一个平滑进度，避免进度条不动
                on_progress(min(0.9, (time.monotonic() - t0) / 60))
            await asyncio.sleep(poll)

    async def download(self, f: dict) -> bytes:
        params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
        return (await self._get("/view", params=params, timeout=300)).content

    async def upload_image(self, name: str, data: bytes) -> str:
        r = await self.http.post("/upload/image", files={"image": (name, data)}, data={"overwrite": "true"}, timeout=120)
        if r.status_code >= 400:
            raise ComfyError(_err_text(r))
        j = r.json()
        return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    async def interrupt(self):
        try:
            await self.http.post("/interrupt", timeout=5)
        except httpx.HTTPError:
            pass


def collect_outputs(entry: dict) -> list[dict]:
    files = []
    for out in (entry.get("outputs") or {}).values():
        for f in (out.get("images") or []) + (out.get("gifs") or []) + (out.get("videos") or []):
            if f.get("filename") and f.get("type") != "temp":
                files.append({**f, "video": bool(VIDEO_EXT.search(f["filename"]))})
    return files


def spec_of(d, name):
    inp = (d or {}).get("input") or {}
    return (inp.get("required") or {}).get(name) or (inp.get("optional") or {}).get(name)


def choices_of(spec) -> list:
    if not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1:
        return (spec[1] or {}).get("options", [])
    return []


def _is_widget(spec) -> bool:
    return bool(spec) and (isinstance(spec[0], list) or spec[0] in WIDGET_TYPES)


def model_set(info: dict) -> set:
    """object_info 里所有下拉选项 = 该 ComfyUI 已有的模型文件。"""
    out = set()
    for d in info.values():
        inp = d.get("input") or {}
        for spec in list((inp.get("required") or {}).values()) + list((inp.get("optional") or {}).values()):
            for v in choices_of(spec):
                if isinstance(v, str):
                    out.add(v)
                    out.add(re.split(r"[\\/]", v)[-1])
    return out


def missing_node_types(types, info) -> list:
    return sorted({t for t in types if t and t not in NON_EXEC and t not in info})


def to_api_prompt(raw: dict, info: dict) -> dict:
    """UI 格式 → API 格式：按 object_info 的输入顺序消费 widgets_values，连线穿透 Reroute / 旁路节点。"""
    if not isinstance(raw.get("nodes"), list):
        return copy.deepcopy(raw)
    nodes = {n["id"]: n for n in raw["nodes"]}
    links = {}
    for l in raw.get("links") or []:
        if isinstance(l, list):
            links[l[0]] = (l[1], l[2])
        else:
            links[l["id"]] = (l["origin_id"], l["origin_slot"])

    def resolve(link_id, depth=0):
        if link_id not in links or depth > 32:
            return None
        src_id, slot = links[link_id]
        src = nodes.get(src_id)
        if not src:
            return None
        if src.get("type") == "Reroute" or src.get("mode") == 4:
            outs = src.get("outputs") or []
            out_type = outs[slot].get("type") if slot < len(outs) else None
            pas = next((i for i in src.get("inputs") or [] if i.get("link") is not None
                        and (src.get("type") == "Reroute" or i.get("type") == out_type)), None)
            return resolve(pas["link"], depth + 1) if pas else None
        return [str(src["id"]), slot]

    out = {}
    for n in raw["nodes"]:
        if n.get("type") in NON_EXEC or n.get("mode") in (2, 4):
            continue
        d = info.get(n["type"])
        if not d:
            raise ComfyError(f"ComfyUI 缺少节点 {n['type']}，请先安装对应的自定义节点包")
        inp = d.get("input") or {}
        order = list((inp.get("required") or {}).keys()) + list((inp.get("optional") or {}).keys())
        wv = n.get("widgets_values")
        widgets = list(wv) if isinstance(wv, list) else []
        linked = {i["name"]: i["link"] for i in n.get("inputs") or [] if i.get("link") is not None}
        inputs, wi = {}, 0
        for name in order:
            spec = spec_of(d, name)
            if _is_widget(spec):
                if isinstance(wv, list):
                    value = widgets[wi] if wi < len(widgets) else None
                    wi += 1
                    # seed 类输入后面跟着 control_after_generate 的取值，跳过
                    if spec[0] in ("INT", "FLOAT") and wi < len(widgets) and widgets[wi] in CONTROL_WORDS:
                        wi += 1
                else:
                    value = (wv or {}).get(name)
                if name in linked:
                    r = resolve(linked[name])
                    if r:
                        inputs[name] = r
                        continue
                if value is not None:
                    inputs[name] = value
            elif name in linked:
                r = resolve(linked[name])
                if r:
                    inputs[name] = r
        out[str(n["id"])] = {"class_type": n["type"], "inputs": inputs, "_meta": {"title": n.get("title") or n["type"]}}
    return out


def _find_text(api, nid, depth=0):
    n = api.get(nid)
    if not n or depth > 8:
        return None
    if isinstance(n["inputs"].get("text"), str):
        return nid
    for v in n["inputs"].values():
        if isinstance(v, list) and v:
            r = _find_text(api, v[0], depth + 1)
            if r:
                return r
    return None


def inject_params(api: dict, prompt=None, negative=None, seed=None, width=None, height=None, frames=None, fps=None, image=None) -> dict:
    sampler = next((n for n in api.values() if isinstance(n["inputs"].get("positive"), list)), None)
    if sampler:
        pos = _find_text(api, sampler["inputs"]["positive"][0])
        if pos and prompt:
            api[pos]["inputs"]["text"] = prompt
        neg_ref = sampler["inputs"].get("negative")
        neg = _find_text(api, neg_ref[0]) if isinstance(neg_ref, list) else None
        if neg and negative and neg != pos:
            api[neg]["inputs"]["text"] = negative
    elif prompt:
        t = next((n for n in api.values() if isinstance(n["inputs"].get("text"), str)), None)
        if t:
            t["inputs"]["text"] = prompt
    num = (int, float)
    for n in api.values():
        i = n["inputs"]
        if seed is not None:
            for k in ("seed", "noise_seed"):
                if isinstance(i.get(k), num):
                    i[k] = seed
        if width and isinstance(i.get("width"), num) and isinstance(i.get("height"), num):
            i["width"], i["height"] = width, height
        if frames:
            for k in ("length", "frames", "num_frames", "video_frames"):
                if isinstance(i.get(k), num):
                    i[k] = frames
        if fps:
            for k in ("fps", "frame_rate"):
                if isinstance(i.get(k), num):
                    i[k] = fps
        if image and n["class_type"] == "LoadImage":
            i["image"] = image
    return api


def default_graph(ckpt: str) -> dict:
    """没导入工作流时用的最小文生图图。"""
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, blurry, watermark", "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 25, "cfg": 6.5, "sampler_name": "euler", "scheduler": "normal",
                                                   "denoise": 1, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lumen", "images": ["8", 0]}},
    }


def first_checkpoint(info: dict) -> str | None:
    c = choices_of(spec_of(info.get("CheckpointLoaderSimple"), "ckpt_name"))
    return c[0] if c else None
