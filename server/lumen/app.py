"""HTTP 接口：REST 操作 + SSE 状态推送。启动：python -m lumen（见 __main__.py）。

多账号：登录后用 HttpOnly 会话 Cookie；每个请求按会话找到该账号自己的 Engine。
"""
import asyncio
import contextvars
import hmac
import json
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from .accounts import AccountError
from .engine import ActionError, Engine
from .hub import Hub
from .workflow import WorkflowError

MAX_UPLOAD = 20 << 20
MAX_RESULT = 200 << 20
FILE_RE = re.compile(r"^[\w-]{1,80}\.[a-z0-9]{1,5}$")
RESULT_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"}
COOKIE = "lumen_session"
OPEN = {"/api/health", "/api/setup", "/api/login", "/api/logout"}

_engine: contextvars.ContextVar[Engine] = contextvars.ContextVar("engine")


def E() -> Engine:
    return _engine.get()


async def bind(request: Request):
    """把当前账号的 Engine 放进上下文，路由里用 E() 取。"""
    e = getattr(request.state, "engine", None)
    if e is not None:
        _engine.set(e)


def _https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _set_session(res, request, token):
    res.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=_https(request), max_age=30 * 86400, path="/")


def create_app(hub: Hub | None = None, start_engine: bool = True) -> FastAPI:
    hub = hub or Hub()
    accounts = hub.accounts

    @asynccontextmanager
    async def lifespan(_app):
        if start_engine:
            hub.start()
        yield
        await hub.shutdown()

    app = FastAPI(title="Lumen", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(bind)])
    app.state.hub = hub

    # ---------- 会话：除少数开放接口外，/api 都要登录 ----------
    @app.middleware("http")
    async def session(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api") and path not in OPEN:
            token = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            user = accounts.session_user(token)
            if not user:
                return JSONResponse({"detail": "需要登录"}, status_code=401)
            if path.startswith("/api/admin") and user["role"] != "admin":
                return JSONResponse({"detail": "只有管理员可以操作"}, status_code=403)
            request.state.user = user
            request.state.token = token
            request.state.engine = hub.engine(user["id"])
        return await call_next(request)

    @app.exception_handler(ActionError)
    async def action_error(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(WorkflowError)
    async def wf_error(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(AccountError)
    async def account_error(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # ---------- 账号 ----------
    @app.get("/api/health")
    async def health():
        return {"ok": True, "setup": accounts.needs_setup(), "setupCode": bool(C.TOKEN), "public": C.PUBLIC}

    @app.post("/api/setup")
    async def setup(body: dict, request: Request):
        """第一次启动：创建管理员。公开部署要求填写初始化口令（LUMEN_TOKEN）。"""
        if not accounts.needs_setup():
            raise HTTPException(409, "已经初始化过了，请直接登录")
        if C.TOKEN and not hmac.compare_digest(str(body.get("setupCode", "")).encode(), C.TOKEN.encode()):
            raise HTTPException(403, "初始化口令不对")
        if C.PUBLIC and not C.TOKEN:
            raise HTTPException(403, "公开部署必须设置 LUMEN_TOKEN 才能初始化")
        user = accounts.create_user(body.get("username"), body.get("password"), role="admin")
        hub.adopt_legacy(user["id"])
        res = JSONResponse({"user": user})
        _set_session(res, request, accounts.new_session(user["id"]))
        return res

    @app.post("/api/login")
    async def login(body: dict, request: Request):
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
        try:
            token, user = accounts.login(body.get("username"), body.get("password"), ip)
        except AccountError as e:
            raise HTTPException(429 if "次数过多" in str(e) else 401, str(e))
        res = JSONResponse({"user": user})
        _set_session(res, request, token)
        return res

    @app.post("/api/logout")
    async def logout(request: Request):
        accounts.logout(request.cookies.get(COOKIE))
        res = JSONResponse({"ok": True})
        res.delete_cookie(COOKIE, path="/")
        return res

    @app.get("/api/me")
    async def me(request: Request):
        return {"user": request.state.user}

    @app.post("/api/me/password")
    async def change_password(body: dict, request: Request):
        u = request.state.user
        if not accounts.check_password(u["id"], body.get("old")):
            raise HTTPException(400, "原密码不对")
        accounts.set_password(u["id"], body.get("new"))
        res = JSONResponse({"ok": True})
        _set_session(res, request, accounts.new_session(u["id"]))   # 其他设备下线，当前设备保持登录
        return res

    # ---------- 管理员：建账号、停用、重置密码 ----------
    @app.get("/api/admin/users")
    async def list_users():
        return {"users": accounts.users()}

    @app.post("/api/admin/users")
    async def create_user(body: dict):
        role = "admin" if body.get("role") == "admin" else "user"
        return {"user": accounts.create_user(body.get("username"), body.get("password"), role=role)}

    @app.patch("/api/admin/users/{uid}")
    async def update_user(uid: str, body: dict, request: Request):
        if "disabled" in body:
            if uid == request.state.user["id"] and body["disabled"]:
                raise HTTPException(400, "不能停用自己")
            accounts.set_disabled(uid, bool(body["disabled"]))
            if body["disabled"]:
                await hub.stop_user(uid)
            elif hub.running:
                hub.engine(uid)
        if body.get("password"):
            accounts.set_password(uid, body["password"])
        return {"user": accounts.get(uid)}
    # ---------- 状态 ----------
    @app.get("/api/state")
    async def state():
        return E().store.snapshot()

    @app.get("/api/events")
    async def events(request: Request):
        engine = request.state.engine   # 生成器在别的任务里跑，直接绑定，不依赖上下文

        async def stream():
            sent_version, sent_notice = -1, engine.notice_seq
            idle = 0
            while not await request.is_disconnected():
                if engine.store.version != sent_version:
                    sent_version = engine.store.version
                    yield f"event: state\ndata: {json.dumps(engine.store.snapshot(), ensure_ascii=False)}\n\n"
                    for seq, msg in list(engine.notices):
                        if seq > sent_notice:
                            sent_notice = seq
                            yield f"event: notice\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    idle = 0
                else:
                    idle += 1
                    if idle % 60 == 0:
                        yield ": ping\n\n"
                await asyncio.sleep(0.25)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------- 平台 ----------
    @app.post("/api/providers/{pid}/connect")
    async def connect(pid: str, body: dict):
        E().connect(pid, body.get("config") or {}, bool(body.get("demo")))
        if body.get("deploy"):
            E().deploy_comfy([pid])
        return {"ok": True}

    @app.post("/api/providers/{pid}/test")
    async def test(pid: str, body: dict):
        try:
            return await E().test(pid, body.get("config") or {}, bool(body.get("demo")))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "text": str(e) or e.__class__.__name__}

    @app.post("/api/providers/{pid}/disconnect")
    async def disconnect(pid: str):
        E().disconnect(pid)
        return {"ok": True}

    @app.patch("/api/providers/{pid}")
    async def patch_provider(pid: str, body: dict):
        if "gpu" in body:
            E().set_gpu(pid, body["gpu"])
        if body.get("active"):
            E().set_active(pid)
        return {"ok": True}

    @app.post("/api/providers/deploy")
    async def deploy(body: dict):
        E().deploy_comfy(body.get("ids") or [])
        return {"ok": True}

    @app.post("/api/providers/{pid}/start")
    async def start(pid: str):
        E().start_provider(pid)
        return {"ok": True}

    @app.post("/api/providers/{pid}/stop")
    async def stop(pid: str):
        E().stop_provider(pid)
        return {"ok": True}

    @app.patch("/api/policy")
    async def policy(body: dict):
        E().set_policy(body)
        return {"ok": True}
    # ---------- 工作流 ----------
    @app.post("/api/workflows")
    async def import_workflow(file: UploadFile = File(...)):
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "文件超过 20MB")
        try:
            raw = json.loads(data)
        except ValueError:
            raise HTTPException(400, f"{file.filename} 不是有效的 JSON")
        wf = E().import_workflow(raw, file.filename or "workflow.json")
        return {"id": wf["id"], "models": len(wf["models"]), "customNodes": len(wf["customNodes"])}

    @app.delete("/api/workflows/{wf_id}")
    async def remove_workflow(wf_id: str):
        E().remove_workflow(wf_id)
        return {"ok": True}

    @app.post("/api/workflows/{wf_id}/deploy")
    async def deploy_workflow(wf_id: str, body: dict):
        E().deploy_workflow(wf_id, body.get("ids") or [])
        return {"ok": True}

    @app.patch("/api/workflows/{wf_id}/models")
    async def model_source(wf_id: str, body: dict):
        E().set_model_source(wf_id, body.get("name"), body.get("source"))
        return {"ok": True}

    # ---------- 上传与批量 ----------
    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(400, "只能上传图片")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "图片超过 20MB")
        ext = (file.filename or "").rsplit(".", 1)[-1].lower()
        name = f"{uuid.uuid4().hex}.{ext if ext in ('png', 'jpg', 'jpeg', 'webp') else 'png'}"
        (E().store.uploads / name).write_bytes(data)
        return {"id": name}

    @app.post("/api/batches")
    async def create_batch(body: dict):
        b = E().create_batch(body)
        return {"id": b["id"], "jobs": len(b["jobs"])}

    @app.post("/api/batches/{batch_id}/cancel")
    async def cancel(batch_id: str):
        E().cancel_batch(batch_id)
        return {"ok": True}

    @app.post("/api/batches/clear")
    async def clear():
        E().clear_batches()
        return {"ok": True}

    # ---------- 作品（文件只能被所属账号读取） ----------
    @app.delete("/api/gallery/{item_id}")
    async def remove_item(item_id: str):
        E().remove_gallery(item_id)
        return {"ok": True}

    @app.put("/api/compare")
    async def compare(body: dict):
        E().set_compare(body.get("a"), body.get("b"))
        return {"ok": True}

    @app.get("/api/files/{name}")
    async def files(name: str):
        path = E().store.outputs / name
        if not FILE_RE.match(name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})
    # ---------- 浏览器直连本机 ComfyUI（服务器不访问本机地址） ----------
    @app.post("/api/bridge/connect")
    async def bridge_connect(request: Request):
        body = await request.body()
        if len(body) > 50 << 20:
            raise HTTPException(413, "节点列表太大")
        try:
            j = json.loads(body)
        except ValueError:
            raise HTTPException(400, "格式不对")
        info = E().bridge_connect(j.get("endpoint"), j.get("stats") or {}, j.get("objectInfo") or {})
        return {"ok": True, "info": info}

    @app.post("/api/bridge/poll")
    async def bridge_poll():
        return E().bridge_poll()

    @app.post("/api/bridge/jobs/{jid}/progress")
    async def bridge_progress(jid: str, body: dict):
        E().bridge_progress(jid, body.get("value", 0))
        return {"ok": True}

    @app.post("/api/bridge/jobs/{jid}/fail")
    async def bridge_fail(jid: str, body: dict):
        E().bridge_finish(jid, error=body.get("error") or "本机 ComfyUI 出错")
        return {"ok": True}

    @app.post("/api/bridge/jobs/{jid}/result")
    async def bridge_result(jid: str, file: UploadFile = File(...), video: str = Form("0")):
        ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
        if ext not in RESULT_EXT:
            raise HTTPException(400, "不支持的结果格式")
        data = await file.read(MAX_RESULT + 1)
        if len(data) > MAX_RESULT:
            raise HTTPException(413, "结果文件超过 200MB")
        E().bridge_finish(jid, result={"data": data, "ext": ext, "video": video == "1"})
        return {"ok": True}

    @app.get("/api/bridge/uploads/{name}")
    async def bridge_upload(name: str):
        path = E().store.uploads / name
        if not FILE_RE.match(name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # 生产模式：托管前端构建产物（npm run build 后）
    if C.DIST.is_dir():
        app.mount("/", StaticFiles(directory=C.DIST, html=True), name="web")

    return app
