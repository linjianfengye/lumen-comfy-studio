"""调度循环（Engine 的一部分）：电源状态机 off → starting → running → idle → stopping → off、出图、计费。"""
import asyncio
import time
import uuid
from datetime import date

from . import config as C
from .adapters import JobSpec

now_ms = lambda: int(time.time() * 1000)


class SchedulerMixin:
    def _next_job(self, pid):
        if not self.comfy_ready(pid):
            return None
        for b in reversed(self.s["batches"]):
            if b["status"] != "active":
                continue
            wf = self.store.workflow(b["workflowId"]) if b["workflowId"] else None
            if wf and not self.ready_on(wf, pid):
                continue
            j = next((x for x in b["jobs"] if x["status"] == "queued" and x["providerId"] == pid), None)
            if j:
                return b, j
        return None

    def _requeue_current(self, pid):
        cur = self.s["runtime"][pid].get("current")
        if not cur:
            return
        b = self.store.batch(cur["batchId"])
        j = next((x for x in (b or {}).get("jobs", []) if x["id"] == cur["jobId"]), None)
        if j and j["status"] == "running":
            j.update(status="queued", progress=0)
        self.s["runtime"][pid]["current"] = None

    def _power_on(self, pid):
        rt = self.s["runtime"][pid]
        local = C.PROVIDER_BY_ID[pid]["lifecycle"] == "local"
        rt.update(power="starting", startLeft=0 if local else C.PROVIDER_BY_ID[pid]["coldStart"], error=None)
        self.spawn(self._start(pid))

    async def _start(self, pid):
        rt = self.s["runtime"][pid]
        try:
            await self.adapter(pid).start()
            await self.check_pending(pid)
            if rt["power"] == "starting":
                rt.update(power="idle", startLeft=0, idleLeft=self.s["policy"]["idleMinutes"] * 60, failures=0)
        except Exception as e:  # noqa: BLE001
            rt["failures"] = rt.get("failures", 0) + 1
            rt.update(power="off", startLeft=0, error=str(e), retryAt=time.monotonic() + 30)
            if rt["failures"] >= 3:   # 连续失败：让队列里的任务失败，免得一直重试
                for b in self.s["batches"]:
                    for j in b["jobs"]:
                        if j["status"] == "queued" and j["providerId"] == pid:
                            j.update(status="failed", error=f"开机失败：{e}")
                rt["failures"] = 0
            self.notice(f"{C.PROVIDER_BY_ID[pid]['name']} 开机失败：{e}")
        self.touch()

    def _power_off(self, pid):
        self.s["runtime"][pid].update(power="stopping", idleLeft=0)
        self.spawn(self._stop(pid))

    async def _stop(self, pid):
        try:
            await self.adapter(pid).stop()
        except Exception as e:  # noqa: BLE001
            self.notice(f"{C.PROVIDER_BY_ID[pid]['name']} 关机失败，请到平台控制台确认：{e}")
        self.s["runtime"][pid].update(power="off", startLeft=0)
        self.touch()

    def _run_next(self, pid) -> bool:
        nx = self._next_job(pid)
        if not nx:
            return False
        b, j = nx
        j.update(status="running", progress=0, startedAt=now_ms())
        self.s["runtime"][pid]["current"] = {"batchId": b["id"], "jobId": j["id"]}
        self.spawn(self._run_job(pid, b["id"], j["id"]))
        return True

    def _job_spec(self, b, j, wf) -> JobSpec:
        w, _, h = (b.get("size") or "").partition("x")
        v = b.get("video") or {}
        ff = C.UPLOADS / b["firstFrameId"] if b.get("firstFrameId") else None
        return JobSpec(kind=b["kind"], prompt=j["prompt"], seed=j["seed"], raw=self.store.get_raw(wf["id"]) if wf else None,
                       negative=b.get("negative", ""), width=int(w) if w.isdigit() else None, height=int(h) if h.isdigit() else None,
                       frames=v.get("frames"), fps=v.get("fps"), first_frame=ff if ff and ff.exists() else None)
    async def _run_job(self, pid, batch_id, job_id):
        b = self.store.batch(batch_id)
        j = next(x for x in b["jobs"] if x["id"] == job_id)
        t0 = time.monotonic()
        wf = self.store.workflow(b["workflowId"]) if b["workflowId"] else None
        v = b.get("video") or {}

        def progress(x):
            if j["status"] == "running":
                j.update(progress=round(x, 3), seconds=round(time.monotonic() - t0, 1))
                self.touch()

        try:
            spec = self._job_spec(b, j, wf)
            res = await self.adapter(pid).run_job(spec, progress, lambda: j["status"] != "running")
            if j["status"] != "running":
                return
            item_id = str(uuid.uuid4())
            item = {"id": item_id, "prompt": j["prompt"], "kind": b["kind"], "w": spec.width or 1, "h": spec.height or 1,
                    "duration": v.get("seconds") if b["kind"] == "video" else None, "fps": v.get("fps"),
                    "seed": j["seed"], "workflow": wf["name"] if wf else "默认工作流", "provider": pid, "gpu": j["gpu"],
                    "seconds": round(res.get("seconds") or time.monotonic() - t0, 1), "createdAt": now_ms(), "batchId": b["id"]}
            if "data" in res:
                item["file"] = f"{item_id}{res['ext']}"
                (C.OUTPUTS / item["file"]).write_bytes(res["data"])
                item.update(src=f"/api/files/{item['file']}", real=True)
            else:
                item.update(src=res["src"], real=False)
            j.update(status="done", progress=1, seconds=item["seconds"], resultId=item_id)
            self.s["gallery"].insert(0, item)
            if b.get("compare") and len(b["jobs"]) >= 2 and all(x["status"] == "done" for x in b["jobs"]):
                self.s["compare"] = {"a": b["jobs"][0]["resultId"], "b": b["jobs"][1]["resultId"]}
                self.notice("对比图已生成，可到「色彩对比」查看")
        except asyncio.CancelledError:
            if j["status"] == "running":
                j["status"] = "cancelled"
        except Exception as e:  # noqa: BLE001
            if j["status"] == "running":
                j.update(status="failed", error=str(e) or e.__class__.__name__)
                self.notice(f"任务失败：{j['error']}")
        finally:
            cur = self.s["runtime"][pid].get("current")
            if cur and cur["jobId"] == job_id:
                self.s["runtime"][pid]["current"] = None
            self.touch()

    def _tick_provider(self, pid, dt):
        rt = self.s["runtime"][pid]
        local = C.PROVIDER_BY_ID[pid]["lifecycle"] == "local"
        power = rt["power"]
        if power == "off":
            over = not local and self.s["spend"]["today"] >= self.s["policy"]["dailyBudget"]
            if self.comfy_ready(pid) and not over and time.monotonic() >= rt.get("retryAt", 0) and self._next_job(pid):
                self._power_on(pid)
                self.touch()
        elif power in ("starting", "stopping"):
            if rt["startLeft"] > 0:
                rt["startLeft"] = max(0, rt["startLeft"] - dt)
                self.touch()
        elif power == "running":
            if not rt.get("current") and not self._run_next(pid):
                rt.update(power="idle", idleLeft=self.s["policy"]["idleMinutes"] * 60)
            self.touch()
        elif power == "idle":
            if self._run_next(pid):
                rt["power"] = "running"
            elif not local and self.s["policy"]["autoShutdown"]:
                scale = C.DEMO_TIME_SCALE if self.store.provider(pid)["demo"] else 1
                rt["idleLeft"] = max(0, rt["idleLeft"] - dt * scale)
                if rt["idleLeft"] <= 0:
                    self._power_off(pid)
            self.touch()

    def _tick_billing(self, dt):
        add = 0.0
        for pid, rt in self.s["runtime"].items():
            if C.PROVIDER_BY_ID[pid]["lifecycle"] != "local" and rt["power"] in ("starting", "running", "idle"):
                add += C.price_of(pid, self.store.provider(pid)["gpu"]) * C.USD_TO_CNY * dt / 3600
        if add:
            self.s["spend"]["today"] = round(self.s["spend"]["today"] + add, 6)

    def _tick_batches(self):
        for b in self.s["batches"]:
            if b["status"] == "active" and not any(j["status"] in ("queued", "running") for j in b["jobs"]):
                b.update(status="done", finishedAt=now_ms())
                self.touch()

    def _tick_day(self):
        today = date.today().isoformat()
        sp = self.s["spend"]
        if sp["day"] != today:
            sp["history"] = (sp.get("history", []) + [{"day": sp["day"], "total": round(sp["today"], 2)}])[-30:]
            sp.update(day=today, today=0.0)
            self.touch()

    def tick(self, dt):
        self._tick_day()
        for pid in self.s["runtime"]:
            self._tick_provider(pid, dt)
        self._tick_billing(dt)
        self._tick_batches()

    async def _resync(self):
        """重启后：Vast 实例若仍在开机，标记为空闲，让倒计时把它关掉，避免一直计费。"""
        p = self.s["providers"]["vast"]
        if p["connected"] and not p["demo"] and self.comfy_ready("vast"):
            try:
                if await self.adapter("vast").is_running():
                    self.s["runtime"]["vast"].update(power="idle", idleLeft=self.s["policy"]["idleMinutes"] * 60)
                    self.touch()
            except Exception:  # noqa: BLE001
                pass

    async def run(self):
        self.spawn(self._resync())
        last = saved = time.monotonic()
        while True:
            await asyncio.sleep(C.TICK_SECONDS)
            t = time.monotonic()
            self.tick(min(t - last, 5))
            last = t
            if t - saved > 2:
                self.store.save()
                saved = t

    def start(self):
        self._loop_task = asyncio.get_running_loop().create_task(self.run())

    async def shutdown(self):
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self.tasks):
            t.cancel()
        self.store.save()
        for ad in self.adapters.values():
            await ad.close()
