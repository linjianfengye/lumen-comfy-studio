"""多账号：每个账号一套独立的 Store + Engine（平台密钥、工作流、批量任务、作品、花费互不可见）。

目录：data/users/<user_id>/{lumen.db, outputs/, uploads/, deploy/}；加密密钥 data/secret.key 全局一份。
所有账号的调度循环都在后台运行，关掉网页后云平台的任务照常调度、到点关机。
"""
import shutil

from . import config as C
from .accounts import Accounts
from .engine import Engine
from .store import Store


class Hub:
    def __init__(self, accounts: Accounts | None = None, users_dir=None, legacy_dir=None):
        self.accounts = accounts or Accounts(C.ACCOUNTS_DB)
        self.users_dir = users_dir or C.USERS
        self.legacy_dir = legacy_dir or C.DATA   # 单账号版本的数据目录
        self.engines: dict[str, Engine] = {}
        self.running = False

    def engine(self, uid: str) -> Engine:
        e = self.engines.get(uid)
        if e is None:
            d = self.users_dir / uid
            d.mkdir(parents=True, exist_ok=True)
            e = self.engines[uid] = Engine(Store(d / "lumen.db"))
            if self.running:
                e.start()
        return e

    def start(self):
        self.running = True
        for uid in self.accounts.active_ids():
            self.engine(uid).start()

    async def stop_user(self, uid):
        """停用账号：取消排队任务、关掉开着的实例，再停掉它的调度循环。"""
        e = self.engines.pop(uid, None)
        if not e:
            return
        for b in e.s["batches"]:
            if b["status"] == "active":
                e.cancel_batch(b["id"])
        for pid, rt in e.s["runtime"].items():
            if rt["power"] not in ("off", "stopping"):
                e.stop_provider(pid)
        await e.shutdown()

    async def shutdown(self):
        for e in list(self.engines.values()):
            await e.shutdown()
        self.engines.clear()

    def adopt_legacy(self, uid):
        """单账号版本留下的 data/lumen.db 等，归到第一个管理员名下。"""
        legacy = self.legacy_dir / "lumen.db"
        if not legacy.exists():
            return
        d = self.users_dir / uid
        d.mkdir(parents=True, exist_ok=True)
        if (d / "lumen.db").exists():
            return
        shutil.move(str(legacy), str(d / "lumen.db"))
        for name in ("outputs", "uploads", "deploy"):
            src = self.legacy_dir / name
            if src.is_dir():
                dst = d / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(src), str(dst))
