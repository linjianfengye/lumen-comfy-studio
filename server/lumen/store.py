"""状态存储：内存里是一份与前端同构的状态，变更后写入 SQLite。

- 平台密钥用 Fernet 加密后落盘，密钥文件 data/secret.key 只在本机
- 给前端的快照会把密钥替换成掩码，前端永远拿不到明文
"""
import asyncio
import copy
import json
import os
import sqlite3
import time
from datetime import date
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config as C

PERSIST_KEYS = ["providers", "policy", "activeProvider", "modelCache", "workflows", "batches", "gallery", "spend", "compare"]
MASK = "••••••"
GALLERY_IN_SNAPSHOT = 500


def _fernet() -> Fernet:
    if not C.KEY_PATH.exists():
        C.KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            os.chmod(C.KEY_PATH, 0o600)
        except OSError:
            pass
    return Fernet(C.KEY_PATH.read_bytes())


def _secret_keys(pid: str) -> set:
    return {f["key"] for f in C.PROVIDER_BY_ID.get(pid, {}).get("fields", []) if f.get("secret")}


def _sample_gallery():
    items = [
        ("lake", "misty alpine lake at dawn, cinematic, film grain", "image", 16, 9),
        ("perfume", "frosted glass perfume bottle, stone pedestal, soft daylight", "image", 3, 4),
        ("city", "city skyline at blue hour, light haze", "video", 1, 1),
        ("portrait", "editorial portrait, soft window light, film tones", "image", 3, 4),
        ("tea", "ceramic tea cup with steam, linen, morning light", "image", 4, 3),
        ("dune", "orange tree on white sand dune, pale sky", "image", 3, 4),
    ]
    now = int(time.time() * 1000)
    return [{
        "id": f"seed-{i}", "src": f"/samples/{n}.webp", "prompt": p, "kind": k, "w": w, "h": h,
        "duration": 4 if k == "video" else None, "seed": 1000 + i * 7919, "workflow": "示例", "provider": "demo",
        "gpu": "—", "seconds": 9 + i * 1.7, "createdAt": now - (i + 1) * 3600_000, "sample": True,
    } for i, (n, p, k, w, h) in enumerate(items)]


def initial_state() -> dict:
    return {
        "providers": {p["id"]: {
            "connected": False, "demo": False, "config": {},
            "gpu": p["gpus"][min(1, len(p["gpus"]) - 1)]["id"],
            "comfy": {"status": "none", "progress": 0},
        } for p in C.PROVIDERS},
        "runtime": {p["id"]: {"power": "off", "startLeft": 0, "idleLeft": 0, "current": None} for p in C.PROVIDERS},
        "activeProvider": "local",
        "policy": {"autoShutdown": True, "idleMinutes": 5, "prewarm": False, "dailyBudget": 20, "autoDeployModels": True,
                   "verifyDeploy": False},
        "modelCache": {},
        "workflows": [],
        "batches": [],
        "gallery": _sample_gallery(),
        "spend": {"day": date.today().isoformat(), "today": 0.0, "history": []},
        "compare": {"a": "seed-0", "b": "seed-5"},
    }


class Store:
    def __init__(self, path=C.DB_PATH):
        # 每个账号一个目录：lumen.db + outputs/ uploads/ deploy/；加密密钥全局一份（C.KEY_PATH）
        root = Path(path).parent
        self.outputs, self.uploads, self.deploy = root / "outputs", root / "uploads", root / "deploy"
        for d in (self.outputs, self.uploads, self.deploy):
            d.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS raw (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.fernet = _fernet()
        self.state = self._load()
        self.version = 0
        self.dirty = False
        self.changed = asyncio.Event()

    # ---------- 读写 ----------
    def _load(self) -> dict:
        s = initial_state()
        for key, value in self.db.execute("SELECT key, value FROM kv"):
            if key in PERSIST_KEYS:
                s[key] = json.loads(value)
        base = initial_state()
        # 目录新增平台或字段时补齐
        for pid, p in base["providers"].items():
            s["providers"][pid] = {**p, **s["providers"].get(pid, {})}
        s["policy"] = {**base["policy"], **s["policy"]}
        for g in s["gallery"]:   # 示例图已从 png 换成 webp
            if g.get("src", "").startswith("/samples/") and g["src"].endswith(".png"):
                g["src"] = g["src"][:-4] + ".webp"
        for pid, p in s["providers"].items():
            p["config"] = self._decrypt(pid, p.get("config", {}))
            if p["comfy"].get("status") == "deploying":   # 重启时中断的部署
                p["comfy"] = {"status": "error", "progress": 0, "error": "后端重启，部署被中断，请重试"}
        for b in s["batches"]:
            for j in b["jobs"]:
                if j["status"] == "running":
                    j.update(status="queued", progress=0)
        for w in s["workflows"]:
            for t in (w.get("targets") or {}).values():
                if t.get("status") == "deploying":
                    t.update(status="error", error="后端重启，部署被中断，请重试")
        if s["spend"].get("day") != date.today().isoformat():
            s["spend"] = {"day": date.today().isoformat(), "today": 0.0, "history": s["spend"].get("history", [])[-30:]}
        return s

    def _encrypt(self, pid, cfg):
        keys = _secret_keys(pid)
        return {k: ("enc:" + self.fernet.encrypt(v.encode()).decode() if k in keys and v else v) for k, v in cfg.items()}

    def _decrypt(self, pid, cfg):
        out = {}
        for k, v in cfg.items():
            if isinstance(v, str) and v.startswith("enc:"):
                try:
                    v = self.fernet.decrypt(v[4:].encode()).decode()
                except InvalidToken:
                    v = ""   # 密钥文件换了，密文作废
            out[k] = v
        return out

    def save(self):
        if not self.dirty:
            return
        s = self.state
        rows = []
        for key in PERSIST_KEYS:
            v = s[key]
            if key == "providers":
                v = {pid: {**p, "config": self._encrypt(pid, p["config"])} for pid, p in v.items()}
            rows.append((key, json.dumps(v, ensure_ascii=False)))
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", rows)
        self.dirty = False

    def touch(self):
        self.version += 1
        self.dirty = True
        self.changed.set()

    # ---------- 原始工作流 JSON（体积大，单独存，不进快照） ----------
    def put_raw(self, wf_id, raw):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO raw (id, value) VALUES (?, ?)", (wf_id, json.dumps(raw)))

    def get_raw(self, wf_id):
        row = self.db.execute("SELECT value FROM raw WHERE id = ?", (wf_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def del_raw(self, wf_id):
        with self.db:
            self.db.execute("DELETE FROM raw WHERE id = ?", (wf_id,))

    # ---------- 查询 ----------
    def provider(self, pid):
        return self.state["providers"][pid]

    def workflow(self, wf_id):
        return next((w for w in self.state["workflows"] if w["id"] == wf_id), None)

    def batch(self, batch_id):
        return next((b for b in self.state["batches"] if b["id"] == batch_id), None)

    def model_mode(self, pid) -> str:
        """模型如何到达该平台：演示平台一律模拟下载。"""
        p = self.provider(pid)
        return "download" if p["demo"] else C.PROVIDER_BY_ID[pid]["models"]

    def snapshot(self) -> dict:
        s = copy.deepcopy(self.state)
        for pid, p in s["providers"].items():
            keys = _secret_keys(pid)
            p["secretsSet"] = sorted(k for k in keys if p["config"].get(k))
            p["config"] = {k: (MASK if k in keys and v else v) for k, v in p["config"].items()}
            p["modelMode"] = self.model_mode(pid)
        s["gallery"] = s["gallery"][:GALLERY_IN_SNAPSHOT]
        s["version"] = self.version
        return s
