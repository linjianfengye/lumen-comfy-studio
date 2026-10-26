"""调度器：有任务自动开机 → 运算 → 空闲倒计时 → 自动关机；模型部署；计费估算。

所有状态改动都在事件循环线程里完成；耗时操作（部署、开机、出图）作为后台任务运行，完成后回写状态。
"""
import asyncio
import collections
import random
import re
import time
import uuid

from . import config as C
from .adapters import AdapterError, make_adapter
from .adapters.bridge import OBJINFO_KEY, OFFLINE_AFTER, BridgeAdapter
from .adapters.modal_app import PACK_RE, ModalAdapter
from .scheduler import SchedulerMixin
from .store import MASK, Store
from .workflow import basename, parse

FAST_GPU = re.compile(r"H100|A100|A800|L40S|4090")
LOCAL_PUBLIC_MSG = "在线版由浏览器直连你电脑上的 ComfyUI，服务器不会访问本机地址；请在连接窗口里点「保存并连接」"
now_ms = lambda: int(time.time() * 1000)


class ActionError(ValueError):
    """返回给前端的业务错误（HTTP 400）。"""


class Engine(SchedulerMixin):
    def __init__(self, store: Store):
        self.store = store
        self.adapters = {}
        self.tasks = set()
        self.notices = collections.deque(maxlen=50)
        self.notice_seq = 0
        self._loop_task = None
        self.bridge = {}          # 浏览器直连本机 ComfyUI 的待领取任务
        self.bridge_seen = 0.0    # 网页最近一次轮询的时间

    # ---------- 基础 ----------
    @property
    def s(self):
        return self.store.state

    def touch(self):
        self.store.touch()

    def notice(self, msg: str):
        self.notice_seq += 1
        self.notices.append((self.notice_seq, msg))
        self.touch()

    def spawn(self, coro):
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    def adapter(self, pid):
        if pid not in self.adapters:
            p = self.store.provider(pid)
            ad = BridgeAdapter(pid, p) if self.is_bridge(pid) else make_adapter(pid, p)
            ad.deploy_dir = self.store.deploy
            ad.engine = self
            self.adapters[pid] = ad
        return self.adapters[pid]

    def reset_adapter(self, pid):
        ad = self.adapters.pop(pid, None)
        if ad:
            self.spawn(ad.close())

    def comfy_ready(self, pid):
        p = self.store.provider(pid)
        return p["connected"] and p["comfy"]["status"] == "ready"

    # ---------- 浏览器直连本机 ComfyUI ----------
    def is_bridge(self, pid) -> bool:
        p = self.store.provider(pid)
        return pid == "local" and not p["demo"] and bool(p["comfy"].get("bridge"))

    def bridge_online(self) -> bool:
        return time.monotonic() - self.bridge_seen < OFFLINE_AFTER

    def bridge_connect(self, endpoint: str, stats: dict, object_info: dict):
        if not re.match(r"^https?://[^\s/]+(/\S*)?$", endpoint or ""):
            raise ActionError("请填写 http(s) 地址，例如 http://127.0.0.1:8188")
        if not isinstance(object_info, dict) or not object_info:
            raise ActionError("没有读到 ComfyUI 的节点列表")
        dev = ((stats or {}).get("devices") or [{}])[0]
        info = {"device": str(dev.get("name", "本机 GPU"))[:80],
                "vram": round((dev.get("vram_total") or 0) / 2**30, 1),
                "version": str(((stats or {}).get("system") or {}).get("comfyui_version", ""))[:40],
                "nodeCount": len(object_info)}
        self.store.put_raw(OBJINFO_KEY, object_info)
        p = self.store.provider("local")
        p.update(connected=True, demo=False, config={"endpoint": endpoint.rstrip("/")})
        p["comfy"] = {"status": "ready", "progress": 1, "bridge": True, "url": endpoint.rstrip("/"), "info": info}
        self.bridge_seen = time.monotonic()
        self.reset_adapter("local")
        for w in self.s["workflows"]:
            w["checks"].pop("local", None)
            if self.s["policy"]["autoDeployModels"]:
                self.deploy_workflow(w["id"], ["local"])
        self.touch()
        return info

    def bridge_poll(self) -> dict:
        self.bridge_seen = time.monotonic()
        jobs = []
        for e in self.bridge.values():
            if not e["claimed"] and not e["cancel"]:
                e["claimed"] = True
                jobs.append({"id": e["id"], "prompt": e["prompt"], "upload": e["upload"]})
        return {"jobs": jobs, "cancel": [e["id"] for e in self.bridge.values() if e["cancel"]]}

    def bridge_progress(self, jid, value):
        e = self.bridge.get(jid)
        if e and not e["future"].done():
            e["progress"](max(0.0, min(1.0, float(value))))

    def bridge_finish(self, jid, result=None, error=None):
        e = self.bridge.get(jid)
        if not e or e["future"].done():
            raise ActionError("任务不存在或已结束")
        if error is not None:
            e["future"].set_exception(AdapterError(str(error)[:500] or "本机 ComfyUI 出错"))
        else:
            e["future"].set_result(result)

    # ---------- 平台 ----------
    def connect(self, pid, config: dict, demo: bool):
        meta = C.PROVIDER_BY_ID.get(pid)
        if not meta:
            raise ActionError("未知平台")
        if not demo and not meta.get("adapter"):
            raise ActionError(f"{meta['name']} 的真实接入还在开发中，请打开「演示模式」体验")
        if not demo and pid == "local" and C.PUBLIC:
            raise ActionError(LOCAL_PUBLIC_MSG)
        p = self.store.provider(pid)
        old = p["config"]
        cfg = {k: (old.get(k, "") if v == MASK else str(v).strip()) for k, v in (config or {}).items()}
        if not demo:
            missing = [f["label"] for f in meta["fields"] if not f.get("optional") and f.get("type") != "select" and not cfg.get(f["key"])]
            if missing:
                raise ActionError("请填写 " + "、".join(missing))
        changed = cfg != old or p["demo"] != demo or not p["connected"]
        if p["demo"] != demo:
            self.s["modelCache"].pop(pid, None)   # 演示缓存不能冒充真实平台
            for w in self.s["workflows"]:
                w["targets"].pop(pid, None)
                w["checks"].pop(pid, None)
        p.update(connected=True, demo=demo, config=cfg)
        if changed:
            p["comfy"] = {"status": "none", "progress": 0}
        self.reset_adapter(pid)
        self.touch()

    def disconnect(self, pid):
        p = self.store.provider(pid)
        self._requeue_current(pid)
        p.update(connected=False, config={})
        self.s["runtime"][pid].update(power="off", startLeft=0, idleLeft=0, current=None)
        self.reset_adapter(pid)
        self.touch()

    def set_gpu(self, pid, gpu):
        if not any(g["id"] == gpu for g in C.PROVIDER_BY_ID[pid]["gpus"]):
            raise ActionError("该平台没有这个 GPU 型号")
        self.store.provider(pid)["gpu"] = gpu
        self.reset_adapter(pid)
        self.touch()

    def set_active(self, pid):
        self.s["activeProvider"] = pid
        self.touch()

    def set_policy(self, patch: dict):
        allowed = {"autoShutdown": bool, "idleMinutes": int, "prewarm": bool, "dailyBudget": float, "autoDeployModels": bool,
                   "verifyDeploy": bool}
        for k, v in patch.items():
            if k in allowed:
                self.s["policy"][k] = allowed[k](v)
        self.s["policy"]["idleMinutes"] = max(1, min(60, self.s["policy"]["idleMinutes"]))
        if patch.get("prewarm"):
            self.start_provider(self.s["activeProvider"])
        self.touch()

    def start_provider(self, pid):
        rt = self.s["runtime"][pid]
        if self.comfy_ready(pid) and rt["power"] == "off":
            self._power_on(pid)

    def stop_provider(self, pid):
        rt = self.s["runtime"][pid]
        if rt["power"] in ("off", "stopping"):
            return
        self._requeue_current(pid)
        self._power_off(pid)

    async def test(self, pid, config: dict, demo: bool) -> dict:
        """连接前的连通性测试，不改状态。"""
        if demo:
            return {"ok": True, "text": "演示模式：不连接真实平台"}
        if pid == "local" and C.PUBLIC:
            raise ActionError(LOCAL_PUBLIC_MSG)
        old = self.store.provider(pid)["config"]
        cfg = {k: (old.get(k, "") if v == MASK else v) for k, v in (config or {}).items()}
        ad = make_adapter(pid, {"config": cfg, "comfy": {}, "gpu": self.store.provider(pid)["gpu"]})
        ad.deploy_dir = self.store.deploy
        try:
            if pid == "vast":
                inst = await ad._instance()
                return {"ok": True, "text": f"实例 {inst.get('id', cfg.get('instanceId'))} · {inst.get('gpu_name', '')} · {inst.get('actual_status', '')}"}
            if pid == "runpod":
                r = await ad.deploy(lambda *_: None)
                return {"ok": True, "text": f"Endpoint 可用 · workers {r['info'].get('workers')}"}
            if pid == "modal" and not cfg.get("endpoint"):
                code, tail = await ad._run(["app", "list"], lambda _: None, timeout=90)
                if code != 0:
                    raise AdapterError(f"Modal Token 校验失败：{tail[-200:]}")
                return {"ok": True, "text": "Modal Token 有效，可以部署"}
            i = await ad.client().probe(timeout=60 if pid == "modal" else 8)
            vram = f" · {i['vram']} GB" if i["vram"] else ""
            return {"ok": True, "text": f"ComfyUI {i['version']} · {i['device']}{vram} · {i['nodeCount']} 个节点 · {i['checkpoints']} 个 checkpoint"}
        finally:
            await ad.close()

    # ---------- ComfyUI 部署 ----------
    def deploy_comfy(self, ids):
        for pid in ids:
            p = self.store.provider(pid)
            if p["connected"] and p["comfy"]["status"] in ("none", "error") and not self.is_bridge(pid):
                p["comfy"] = {"status": "deploying", "progress": 0, "step": ""}
                self.spawn(self._deploy_comfy(pid))
        self.touch()

    def _node_packs(self, extra_wf=None):
        packs = set()
        for w in self.s["workflows"] + ([extra_wf] if extra_wf else []):
            packs |= {c["pack"] for c in w["customNodes"] if c["pack"] not in ("未知来源", "comfy-core")}
        # 来自用户上传的工作流：不合法的包名直接丢弃，不会写进部署文件
        return sorted(p for p in packs if PACK_RE.match(p))

    async def _deploy_comfy(self, pid):
        p = self.store.provider(pid)
        p["comfy"] = {"status": "deploying", "progress": 0, "step": ""}

        def progress(v, step=""):
            p["comfy"].update(progress=round(v, 3), step=step or p["comfy"].get("step", ""))
            self.touch()

        self.reset_adapter(pid)
        ad = self.adapter(pid)
        try:
            kw = {"node_packs": self._node_packs(), "idle_minutes": self.s["policy"]["idleMinutes"],
                  "verify": self.s["policy"]["verifyDeploy"]} if isinstance(ad, ModalAdapter) else {}
            res = await ad.deploy(progress, **kw)
            p["comfy"] = {"status": "ready", "progress": 1, **res}
            name = C.PROVIDER_BY_ID[pid]["name"]
            self.notice(f"{name} 已连接 · {res.get('info', {}).get('device', '')}" if pid == "local" else f"{name} 的 ComfyUI 已部署，可打开界面")
            if self.s["policy"]["autoDeployModels"]:
                for w in self.s["workflows"]:
                    self.deploy_workflow(w["id"], [pid])
        except Exception as e:  # noqa: BLE001 — 任何失败都要回写给前端
            p["comfy"] = {"status": "error", "progress": 0, "error": str(e) or e.__class__.__name__}
        finally:
            self.reset_adapter(pid)
            self.touch()

    # ---------- 工作流与模型 ----------
    def import_workflow(self, raw: dict, file_name: str) -> dict:
        wf = parse(raw, file_name)
        self.s["workflows"].insert(0, wf)
        self.store.put_raw(wf["id"], raw)
        if self.s["policy"]["autoDeployModels"]:
            self.deploy_workflow(wf["id"], [pid for pid in self.s["providers"] if self.comfy_ready(pid)])
        self.touch()
        return wf

    def remove_workflow(self, wf_id):
        self.s["workflows"] = [w for w in self.s["workflows"] if w["id"] != wf_id]
        self.store.del_raw(wf_id)
        self.touch()

    def set_model_source(self, wf_id, name, url):
        wf = self.store.workflow(wf_id)
        if not wf:
            raise ActionError("工作流不存在")
        if not re.match(r"^https?://\S+$", url or ""):
            raise ActionError("请填写 http(s) 下载地址")
        for m in wf["models"]:
            if m["name"] == name:
                m["source"] = url
        for t in wf["targets"].values():
            if t.get("status") == "error":
                t.update(status="idle", error=None)
        self.touch()

    def ready_on(self, wf, pid) -> bool:
        mode = self.store.model_mode(pid)
        if mode == "manual":
            return True
        if mode == "check":
            c = wf["checks"].get(pid)
            return bool(c) and c["status"] not in ("checking",)
        return all(m["name"] in self.s["modelCache"].get(pid, []) for m in wf["models"])

    def deploy_workflow(self, wf_id, ids):
        wf = self.store.workflow(wf_id)
        if not wf:
            raise ActionError("工作流不存在")
        for pid in ids:
            if not self.comfy_ready(pid):
                continue
            mode = self.store.model_mode(pid)
            if mode == "check":
                if self._powered_off_instance(pid):
                    # 实例关机时不为核对单独开机（会计费），等出图开机后再核对
                    wf["checks"][pid] = {"status": "pending", "missingModels": [], "missingNodes": []}
                    continue
                wf["checks"][pid] = {"status": "checking", "missingModels": [], "missingNodes": []}
                self.spawn(self._check(wf_id, pid))
            elif mode == "manual":
                wf["targets"][pid] = {"status": "ready", "note": "模型需预置在镜像或网络卷中"}
            elif not self.ready_on(wf, pid) and wf["targets"].get(pid, {}).get("status") != "deploying":
                wf["targets"][pid] = {"status": "deploying", "current": None, "progress": 0}
                self.spawn(self._download(wf_id, pid))
        self.touch()

    def _powered_off_instance(self, pid) -> bool:
        return (not self.store.provider(pid).get("demo") and C.PROVIDER_BY_ID[pid]["lifecycle"] == "instance"
                and self.s["runtime"][pid]["power"] in ("off", "stopping"))

    async def check_pending(self, pid):
        """实例开机后补做之前推迟的核对。"""
        for w in self.s["workflows"]:
            if w["checks"].get(pid, {}).get("status") == "pending":
                w["checks"][pid] = {"status": "checking", "missingModels": [], "missingNodes": []}
                await self._check(w["id"], pid)

    async def _check(self, wf_id, pid):
        wf = self.store.workflow(wf_id)
        try:
            res = await self.adapter(pid).check_models(wf, self.store.get_raw(wf_id))
        except Exception as e:  # noqa: BLE001
            res = {"status": "error", "error": str(e), "missingModels": [], "missingNodes": []}
        if self.store.workflow(wf_id):
            wf["checks"][pid] = {**res, "at": now_ms()}
            self.touch()

    async def _download(self, wf_id, pid):
        wf = self.store.workflow(wf_id)
        t = wf["targets"][pid]
        ad = self.adapter(pid)
        try:
            # Modal：工作流带来新的自定义节点包时先重新部署镜像
            if isinstance(ad, ModalAdapter) and ad.needs_redeploy(self._node_packs(), self.s["policy"]["idleMinutes"]):
                t["step"] = "安装自定义节点（重新部署镜像）"
                self.touch()
                await self._deploy_comfy(pid)
                if not self.comfy_ready(pid):
                    raise AdapterError(self.store.provider(pid)["comfy"].get("error") or "重新部署失败")
                ad = self.adapter(pid)
            cache = self.s["modelCache"].setdefault(pid, [])
            if isinstance(ad, ModalAdapter) and any(m["name"] not in cache for m in wf["models"]):
                # 先一次性列出存储卷（不开容器），已有的模型直接记为已部署
                t["step"] = "核对存储卷里已有的模型"
                self.touch()
                try:
                    files = await ad.volume_files()
                except AdapterError:
                    files = set()
                for m in wf["models"]:
                    if m["name"] not in cache and f"{m['dir']}/{basename(m['name'])}" in files:
                        cache.append(m["name"])
                t["step"] = None
            for m in wf["models"]:
                if m["name"] in cache:
                    continue
                t.update(current=m["name"], progress=0, step=None)
                self.touch()

                def progress(v, step=None):
                    t["progress"] = round(v, 3)
                    self.touch()

                await ad.deploy_model(m, progress)
                cache.append(m["name"])
                t.update(current=None, progress=0)
                self.touch()
            t.update(status="ready", current=None, progress=1)
        except Exception as e:  # noqa: BLE001
            t.update(status="error", error=f"{t.get('current') or ''} {e}".strip())
        self.touch()

    # ---------- 批量任务 ----------
    def create_batch(self, body: dict) -> dict:
        ids = [pid for pid in body.get("providerIds") or [] if self.comfy_ready(pid)]
        if not ids:
            raise ActionError("请选择至少一个已部署 ComfyUI 的平台")
        prompts = [p.strip() for p in body.get("prompts") or [] if p.strip()]
        per = max(1, min(64, int(body.get("perPrompt") or 1)))
        if not prompts:
            raise ActionError("至少填写一条提示词")
        wf = self.store.workflow(body.get("workflowId")) if body.get("workflowId") else None
        mode, start = body.get("seedMode", "step"), int(body.get("seedStart") or 0)
        jobs = []
        for pi, prompt in enumerate(prompts):
            for k in range(per):
                seed = start if mode == "fixed" else start + pi * per + k if mode == "step" else random.randrange(2**32)
                pid = ids[len(jobs) % len(ids)]
                jobs.append({"id": str(uuid.uuid4()), "prompt": prompt, "seed": seed, "providerId": pid,
                             "gpu": self.store.provider(pid)["gpu"], "status": "queued", "progress": 0, "seconds": 0})
        b = {
            "id": str(uuid.uuid4()),
            "name": f"{wf['name'] if wf else '默认工作流'} · {time.strftime('%H:%M')}",
            "workflowId": wf["id"] if wf else None, "kind": body.get("kind", "image"), "size": body.get("size", "1024x1024"),
            "providerIds": ids, "negative": body.get("negative", ""), "video": body.get("video"),
            "firstFrameId": body.get("firstFrameId"), "compare": bool(body.get("compare")),
            "jobs": jobs, "createdAt": now_ms(), "status": "active",
        }
        self.s["batches"].insert(0, b)
        if wf:
            self.deploy_workflow(wf["id"], ids)
        self.touch()
        return b

    def cancel_batch(self, batch_id):
        b = self.store.batch(batch_id)
        if b:
            b["status"] = "cancelled"
            for j in b["jobs"]:
                if j["status"] in ("queued", "running"):
                    j["status"] = "cancelled"
            self.touch()

    def clear_batches(self):
        self.s["batches"] = [b for b in self.s["batches"] if b["status"] == "active"]
        self.touch()

    def remove_gallery(self, item_id):
        item = next((g for g in self.s["gallery"] if g["id"] == item_id), None)
        if item and item.get("file"):
            (self.store.outputs / item["file"]).unlink(missing_ok=True)
        self.s["gallery"] = [g for g in self.s["gallery"] if g["id"] != item_id]
        self.touch()

    def set_compare(self, a=None, b=None):
        if a:
            self.s["compare"]["a"] = a
        if b:
            self.s["compare"]["b"] = b
        self.touch()
