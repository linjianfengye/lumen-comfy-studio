"""HTTP 接口：REST 操作 + SSE 状态推送。启动：python -m lumen（见 __main__.py）。"""
import asyncio
import hmac
import json
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from .engine import ActionError, Engine
from .store import Store
from .workflow import WorkflowError

MAX_UPLOAD = 20 << 20
FILE_RE = re.compile(r"^[\w-]{1,80}\.[a-z0-9]{1,5}$")


def create_app(store: Store | None = None, start_engine: bool = True) -> FastAPI:
    engine = Engine(store or Store())

    @asynccontextmanager
    async def lifespan(_app):
        if start_engine:
            engine.start()
        yield
        await engine.shutdown()

    app = FastAPI(title="Lumen", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.engine = engine

    # ---------- 鉴权：设置 LUMEN_TOKEN 后生效；浏览器登录后用 HttpOnly Cookie ----------
    @app.middleware("http")
    async def auth(request: Request, call_next):
        path = request.url.path
        if C.TOKEN and path.startswith("/api") and path not in ("/api/health", "/api/login"):
            got = request.cookies.get("lumen_token") or request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(got.encode(), C.TOKEN.encode()):
                return JSONResponse({"detail": "需要登录"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(ActionError)
    async def action_error(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(WorkflowError)
    async def wf_error(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "auth": bool(C.TOKEN)}

    @app.post("/api/login")
    async def login(body: dict, request: Request):
        if not C.TOKEN:
            return {"ok": True}
        if not hmac.compare_digest(str(body.get("token", "")).encode(), C.TOKEN.encode()):
            raise HTTPException(401, "令牌不对")
        res = JSONResponse({"ok": True})
        # 公网部署在 HTTPS 反代后面，cookie 只走 HTTPS
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        res.set_cookie("lumen_token", C.TOKEN, httponly=True, samesite="strict", secure=https, max_age=30 * 86400)
        return res

    # ---------- 状态 ----------
    @app.get("/api/state")
    async def state():
        return engine.store.snapshot()

    @app.get("/api/events")
    async def events(request: Request):
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
        engine.connect(pid, body.get("config") or {}, bool(body.get("demo")))
        if body.get("deploy"):
            engine.deploy_comfy([pid])
        return {"ok": True}

    @app.post("/api/providers/{pid}/test")
    async def test(pid: str, body: dict):
        try:
            return await engine.test(pid, body.get("config") or {}, bool(body.get("demo")))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "text": str(e) or e.__class__.__name__}

    @app.post("/api/providers/{pid}/disconnect")
    async def disconnect(pid: str):
        engine.disconnect(pid)
        return {"ok": True}

    @app.patch("/api/providers/{pid}")
    async def patch_provider(pid: str, body: dict):
        if "gpu" in body:
            engine.set_gpu(pid, body["gpu"])
        if body.get("active"):
            engine.set_active(pid)
        return {"ok": True}

    @app.post("/api/providers/deploy")
    async def deploy(body: dict):
        engine.deploy_comfy(body.get("ids") or [])
        return {"ok": True}

    @app.post("/api/providers/{pid}/start")
    async def start(pid: str):
        engine.start_provider(pid)
        return {"ok": True}

    @app.post("/api/providers/{pid}/stop")
    async def stop(pid: str):
        engine.stop_provider(pid)
        return {"ok": True}

    @app.patch("/api/policy")
    async def policy(body: dict):
        engine.set_policy(body)
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
        wf = engine.import_workflow(raw, file.filename or "workflow.json")
        return {"id": wf["id"], "models": len(wf["models"]), "customNodes": len(wf["customNodes"])}

    @app.delete("/api/workflows/{wf_id}")
    async def remove_workflow(wf_id: str):
        engine.remove_workflow(wf_id)
        return {"ok": True}

    @app.post("/api/workflows/{wf_id}/deploy")
    async def deploy_workflow(wf_id: str, body: dict):
        engine.deploy_workflow(wf_id, body.get("ids") or [])
        return {"ok": True}

    @app.patch("/api/workflows/{wf_id}/models")
    async def model_source(wf_id: str, body: dict):
        engine.set_model_source(wf_id, body.get("name"), body.get("source"))
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
        (C.UPLOADS / name).write_bytes(data)
        return {"id": name}

    @app.post("/api/batches")
    async def create_batch(body: dict):
        b = engine.create_batch(body)
        return {"id": b["id"], "jobs": len(b["jobs"])}

    @app.post("/api/batches/{batch_id}/cancel")
    async def cancel(batch_id: str):
        engine.cancel_batch(batch_id)
        return {"ok": True}

    @app.post("/api/batches/clear")
    async def clear():
        engine.clear_batches()
        return {"ok": True}

    # ---------- 作品 ----------
    @app.delete("/api/gallery/{item_id}")
    async def remove_item(item_id: str):
        engine.remove_gallery(item_id)
        return {"ok": True}

    @app.put("/api/compare")
    async def compare(body: dict):
        engine.set_compare(body.get("a"), body.get("b"))
        return {"ok": True}

    @app.get("/api/files/{name}")
    async def files(name: str):
        if not FILE_RE.match(name) or not (C.OUTPUTS / name).is_file():
            raise HTTPException(404)
        return FileResponse(C.OUTPUTS / name)

    # 生产模式：托管前端构建产物（npm run build 后）
    if C.DIST.is_dir():
        app.mount("/", StaticFiles(directory=C.DIST, html=True), name="web")

    return app
