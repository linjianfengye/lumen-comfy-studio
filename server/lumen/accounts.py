"""账号与会话：管理员建账号，用户用账号密码登录。

- 密码：scrypt 加盐哈希（hashlib 自带），比较用 hmac.compare_digest
- 会话：随机 32 字节令牌放 HttpOnly Cookie，库里只存它的 SHA-256
- 登录限流：同一 IP 或同一用户名 10 分钟内失败 8 次后暂时拒绝
"""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid

SESSION_DAYS = 30
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
MIN_PASSWORD = 8
_N, _R, _P = 2 ** 14, 8, 1


class AccountError(ValueError):
    pass


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(dk)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), dk)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    def __init__(self, limit=8, window=600):
        self.limit, self.window, self.fails = limit, window, {}

    def blocked(self, *keys) -> bool:
        now = time.monotonic()
        for k in keys:
            self.fails[k] = [t for t in self.fails.get(k, []) if now - t < self.window]
        return any(len(self.fails[k]) >= self.limit for k in keys)

    def fail(self, *keys):
        for k in keys:
            self.fails.setdefault(k, []).append(time.monotonic())

    def reset(self, *keys):
        for k in keys:
            self.fails.pop(k, None)


class Accounts:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user', disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL)""")
        self.limiter = RateLimiter()

    # ---------- 用户 ----------
    def needs_setup(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    @staticmethod
    def _public(row) -> dict:
        return {"id": row["id"], "username": row["username"], "role": row["role"],
                "disabled": bool(row["disabled"]), "createdAt": row["created_at"]}

    def create_user(self, username: str, password: str, role="user") -> dict:
        username = (username or "").strip().lower()
        if not NAME_RE.match(username):
            raise AccountError("用户名需 2–32 位，只能用小写字母、数字和 _ . -，并以字母或数字开头")
        if len(password or "") < MIN_PASSWORD:
            raise AccountError(f"密码至少 {MIN_PASSWORD} 位")
        if role not in ("user", "admin"):
            raise AccountError("角色无效")
        uid = uuid.uuid4().hex
        try:
            with self.db:
                self.db.execute("INSERT INTO users (id, username, password, role, created_at) VALUES (?, ?, ?, ?, ?)",
                                (uid, username, hash_password(password), role, int(time.time() * 1000)))
        except sqlite3.IntegrityError:
            raise AccountError("用户名已存在")
        return self.get(uid)

    def get(self, uid) -> dict | None:
        row = self.db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return self._public(row) if row else None

    def users(self) -> list[dict]:
        return [self._public(r) for r in self.db.execute("SELECT * FROM users ORDER BY created_at")]

    def active_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT id FROM users WHERE disabled = 0")]

    def set_password(self, uid, password):
        if len(password or "") < MIN_PASSWORD:
            raise AccountError(f"密码至少 {MIN_PASSWORD} 位")
        with self.db:
            if not self.db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), uid)).rowcount:
                raise AccountError("用户不存在")
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))   # 改密码后其他设备下线

    def set_disabled(self, uid, disabled: bool):
        u = self.get(uid)
        if not u:
            raise AccountError("用户不存在")
        if disabled and u["role"] == "admin" and sum(1 for x in self.users() if x["role"] == "admin" and not x["disabled"]) <= 1:
            raise AccountError("不能停用最后一个管理员")
        with self.db:
            self.db.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), uid))
            if disabled:
                self.db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

    def check_password(self, uid, password) -> bool:
        row = self.db.execute("SELECT password FROM users WHERE id = ?", (uid,)).fetchone()
        return bool(row) and verify_password(password or "", row["password"])

    # ---------- 登录与会话 ----------
    def login(self, username: str, password: str, ip: str) -> tuple[str, dict]:
        username = (username or "").strip().lower()
        keys = (f"ip:{ip}", f"user:{username}")
        if self.limiter.blocked(*keys):
            raise AccountError("尝试次数过多，请 10 分钟后再试")
        row = self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        # 用户不存在时也做一次哈希，避免通过响应时间判断用户名是否存在
        ok = verify_password(password or "", row["password"] if row else hash_password("x"))
        if not row or not ok or row["disabled"]:
            self.limiter.fail(*keys)
            raise AccountError("账号已停用" if row and ok and row["disabled"] else "用户名或密码不对")
        self.limiter.reset(*keys)
        return self.new_session(row["id"]), self._public(row)

    def new_session(self, uid) -> str:
        token = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
            self.db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                            (_sha(token), uid, int(time.time()) + SESSION_DAYS * 86400))
        return token

    def session_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        row = self.db.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                                 WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0""",
                              (_sha(token), int(time.time()))).fetchone()
        return self._public(row) if row else None

    def logout(self, token: str | None):
        if token:
            with self.db:
                self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))
