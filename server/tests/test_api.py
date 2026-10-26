"""HTTP 接口：初始化管理员、登录与限流、账号隔离、管理员权限、本机 ComfyUI 浏览器直连、安全检查。"""
import ast
import asyncio
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from lumen import config as C
from lumen.accounts import Accounts
from lumen.app import create_app
from lumen.hub import Hub

ADMIN = {"username": "admin", "password": "adminpass1"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def make(tmp_path, monkeypatch):
    def _make(token="", public=False, start=False):
        monkeypatch.setattr(C, "TOKEN", token)
        monkeypatch.setattr(C, "PUBLIC", public)
        hub = Hub(Accounts(tmp_path / "accounts.db"), users_dir=tmp_path / "users", legacy_dir=tmp_path / "legacy")
        return TestClient(create_app(hub, start_engine=start)), hub
    return _make


def setup_admin(c, code=""):
    r = c.post("/api/setup", json={**ADMIN, "setupCode": code})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def login(c, username, password):
    c.cookies.clear()
    return c.post("/api/login", json={"username": username, "password": password})


def test_setup_login_logout(make):
    c, _ = make()
    with c:
        assert c.get("/api/health").json()["setup"] is True
        assert c.get("/api/state").status_code == 401
        setup_admin(c)
        assert c.get("/api/state").status_code == 200            # 初始化后直接登录
        assert c.post("/api/setup", json={**ADMIN, "username": "x"}).status_code == 409
        assert c.post("/api/logout").status_code == 200
        assert c.get("/api/state").status_code == 401
        assert login(c, "admin", "wrongpass1").status_code == 401
        assert login(c, "ADMIN", ADMIN["password"]).status_code == 200   # 用户名不区分大小写
        assert c.get("/api/me").json()["user"]["role"] == "admin"


def test_setup_code_required_when_public(make):
    c, _ = make(token="s3cret-code", public=True)
    with c:
        assert c.get("/api/health").json() == {"ok": True, "setup": True, "setupCode": True, "public": True}
        assert c.post("/api/setup", json={**ADMIN, "setupCode": "nope"}).status_code == 403
        setup_admin(c, "s3cret-code")


def test_public_without_token_cannot_setup(make):
    c, _ = make(token="", public=True)
    with c:
        assert c.post("/api/setup", json=ADMIN).status_code == 403


def test_login_rate_limit(make):
    c, _ = make()
    with c:
        setup_admin(c)
        codes = [login(c, "admin", "wrongpass1").status_code for _ in range(9)]
        assert codes[:8] == [401] * 8 and codes[8] == 429
        assert login(c, "admin", ADMIN["password"]).status_code == 429   # 锁定期内正确密码也拒绝


def test_users_are_isolated(make):
    c, hub = make()
    with c:
        setup_admin(c)
        assert c.post("/api/admin/users", json={"username": "bob", "password": "short"}).status_code == 400
        bob = c.post("/api/admin/users", json={"username": "bob", "password": "bobpass12"}).json()["user"]
        assert c.post("/api/admin/users", json={"username": "bob", "password": "bobpass12"}).status_code == 400

        # admin 导入工作流、连上 Modal（密钥）
        c.post("/api/workflows", files={"file": ("sdxl.json", io.BytesIO(json.dumps(C.SAMPLES["sdxl"]).encode()), "application/json")})
        assert c.post("/api/providers/modal/connect", json={"config": {"tokenId": "ak-admin", "tokenSecret": "as-admin"}}).status_code == 200
        admin_eng = next(e for uid, e in hub.engines.items() if uid != bob["id"])
        (admin_eng.store.outputs / "a1.png").write_bytes(PNG)
        assert c.get("/api/files/a1.png").status_code == 200

        # bob 看不到 admin 的任何东西
        assert login(c, "bob", "bobpass12").status_code == 200
        s = c.get("/api/state").json()
        assert s["workflows"] == [] and not s["providers"]["modal"]["connected"]
        assert "ak-admin" not in json.dumps(s)
        assert c.get("/api/files/a1.png").status_code == 404
        assert c.get("/api/admin/users").status_code == 403
        assert c.post("/api/admin/users", json={"username": "eve", "password": "evepass12"}).status_code == 403

        # admin 视角：密钥只显示掩码
        login(c, "admin", ADMIN["password"])
        s = c.get("/api/state").json()
        assert s["providers"]["modal"]["config"]["tokenSecret"] == "••••••"
        assert "as-admin" not in json.dumps(s)

        # 停用 bob：会话立即失效，也不能再登录
        login(c, "bob", "bobpass12")
        bob_cookie = dict(c.cookies)
        login(c, "admin", ADMIN["password"])
        assert c.patch(f"/api/admin/users/{bob['id']}", json={"disabled": True}).status_code == 200
        c.cookies.clear()
        c.cookies.update(bob_cookie)
        assert c.get("/api/state").status_code == 401
        assert login(c, "bob", "bobpass12").status_code == 401


def test_admin_guards_and_password_change(make):
    c, _ = make()
    with c:
        me = setup_admin(c)
        assert c.patch(f"/api/admin/users/{me['id']}", json={"disabled": True}).status_code == 400   # 不能停用自己
        assert c.post("/api/me/password", json={"old": "wrongpass1", "new": "newpass123"}).status_code == 400
        assert c.post("/api/me/password", json={"old": ADMIN["password"], "new": "newpass123"}).status_code == 200
        assert c.get("/api/state").status_code == 200          # 当前设备保持登录
        assert login(c, "admin", ADMIN["password"]).status_code == 401
        assert login(c, "admin", "newpass123").status_code == 200


def test_file_routes_reject_traversal(make):
    c, _ = make()
    with c:
        setup_admin(c)
        for path in ("/api/files/..%2F..%2Faccounts.db", "/api/files/nope.png", "/api/bridge/uploads/..%2Flumen.db"):
            assert c.get(path).status_code == 404


def test_workflow_import_and_connect_errors(make):
    c, _ = make()
    with c:
        setup_admin(c)
        r = c.post("/api/workflows", files={"file": ("sdxl.json", io.BytesIO(json.dumps(C.SAMPLES["sdxl"]).encode()), "application/json")})
        assert r.status_code == 200 and r.json()["models"] == 3
        bad = c.post("/api/workflows", files={"file": ("x.json", io.BytesIO(b'{"a":1}'), "application/json")})
        assert bad.status_code == 400 and "无法识别" in bad.json()["detail"]
        r = c.post("/api/providers/fal/connect", json={"config": {}, "demo": False})
        assert r.status_code == 400 and "演示模式" in r.json()["detail"]


def test_public_mode_blocks_server_side_local(make):
    c, _ = make(token="code", public=True)
    with c:
        setup_admin(c, "code")
        r = c.post("/api/providers/local/connect", json={"config": {"endpoint": "http://127.0.0.1:8188"}})
        assert r.status_code == 400 and "浏览器直连" in r.json()["detail"]
        assert c.post("/api/providers/local/test", json={"config": {"endpoint": "http://127.0.0.1:8188"}}).json()["ok"] is False


def test_modal_render_cannot_be_injected(tmp_path, monkeypatch):
    from lumen.adapters import modal_app
    from lumen.adapters.base import AdapterError
    ad = modal_app.ModalAdapter("modal", {"config": {}, "comfy": {}, "gpu": "A10G"})
    ad.deploy_dir = tmp_path
    with pytest.raises(AdapterError):
        ad.render(["ok-pack", "x'''; import os; os.system('id') #"])
    # 即使其他字段带引号也跳不出字符串：渲染结果里 CONFIG 仍是一个字符串常量
    monkeypatch.setattr(modal_app, "COMFY_VERSION", "0.1'''+__import__('os').system('id')+'''")
    src = ad.render(["ok-pack"]).read_text(encoding="utf-8")
    tree = ast.parse(src)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "loads")
    assert isinstance(call.args[0], ast.Constant)
    assert json.loads(call.args[0].value)["comfyVersion"].startswith("0.1'''")


def test_netguard_blocks_private_addresses(monkeypatch):
    import httpx
    from lumen import netguard
    monkeypatch.setattr(C, "PUBLIC", True)
    for host in ("127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254", "::1", "localhost"):
        with pytest.raises(netguard.BlockedAddress):
            asyncio.run(netguard.check_host(host, 80))
    asyncio.run(netguard.check_host("8.8.8.8", 443))

    async def go():
        async with httpx.AsyncClient(transport=netguard.transport()) as h:
            await h.get("http://127.0.0.1:8188/system_stats")
    with pytest.raises(httpx.TransportError):
        asyncio.run(go())


OBJECT_INFO = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sdxl.safetensors"]]}}},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}], "clip": ["CLIP"]}}},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}], "batch_size": ["INT", {}]}}},
    "KSampler": {"input": {"required": {"seed": ["INT", {}]}}},
    "VAEDecode": {"input": {"required": {}}},
    "SaveImage": {"input": {"required": {}}},
}


def test_browser_bridge_flow(make, monkeypatch):
    monkeypatch.setattr(C, "TICK_SECONDS", 0.02)
    c, hub = make(token="code", public=True, start=True)
    with c:
        setup_admin(c, "code")
        r = c.post("/api/bridge/connect", json={
            "endpoint": "http://127.0.0.1:8188", "objectInfo": OBJECT_INFO,
            "stats": {"system": {"comfyui_version": "0.3"}, "devices": [{"name": "RTX 4090", "vram_total": 24 * 2**30}]}})
        assert r.status_code == 200 and r.json()["info"]["device"] == "RTX 4090"
        assert c.get("/api/state").json()["providers"]["local"]["comfy"]["bridge"] is True

        assert c.post("/api/batches", json={"prompts": ["a red fox"], "perPrompt": 1, "seedMode": "fixed", "seedStart": 7,
                                            "kind": "image", "size": "512x512", "providerIds": ["local"]}).status_code == 200
        job = None
        for _ in range(200):
            jobs = c.post("/api/bridge/poll").json()["jobs"]
            if jobs:
                job = jobs[0]
                break
            time.sleep(0.02)
        assert job, "网页没有领到任务"
        nodes = job["prompt"]
        assert any(n["class_type"] == "KSampler" and n["inputs"]["seed"] == 7 for n in nodes.values())
        assert any(n["class_type"] == "CLIPTextEncode" and n["inputs"]["text"] == "a red fox" for n in nodes.values())
        assert c.post(f"/api/bridge/jobs/{job['id']}/progress", json={"value": 0.5}).status_code == 200
        r = c.post(f"/api/bridge/jobs/{job['id']}/result", files={"file": ("out.png", io.BytesIO(PNG), "image/png")})
        assert r.status_code == 200

        item = None
        for _ in range(200):
            item = next((g for g in c.get("/api/state").json()["gallery"] if g.get("real")), None)
            if item:
                break
            time.sleep(0.02)
        assert item and item["provider"] == "local"
        assert c.get(item["src"]).content == PNG
        # 结果只能交一次
        assert c.post(f"/api/bridge/jobs/{job['id']}/result", files={"file": ("out.png", io.BytesIO(PNG), "image/png")}).status_code == 400

        # 其他账号拿不到这张图
        c.post("/api/admin/users", json={"username": "bob", "password": "bobpass12"})
        login(c, "bob", "bobpass12")
        assert c.get(item["src"]).status_code == 404


def test_gallery_cap(tmp_path, monkeypatch):
    from lumen.engine import Engine
    from lumen.store import Store
    monkeypatch.setattr(C, "GALLERY_LIMIT", 3)
    e = Engine(Store(tmp_path / "u" / "lumen.db"))
    for i in range(5):
        (e.store.outputs / f"g{i}.png").write_bytes(PNG)
        e.s["gallery"].insert(0, {"id": f"g{i}", "file": f"g{i}.png"})
        e._cap_gallery()
    own = [g["id"] for g in e.s["gallery"] if not g.get("sample")]
    assert own == ["g4", "g3", "g2"]
    assert not (e.store.outputs / "g0.png").exists() and (e.store.outputs / "g4.png").exists()
    assert sum(1 for g in e.s["gallery"] if g.get("sample")) == 6    # 示例不占名额


def test_legacy_data_goes_to_first_admin(make, tmp_path):
    from lumen.store import Store
    legacy = tmp_path / "legacy"
    s = Store(legacy / "lumen.db")
    s.state["workflows"] = [{"id": "w1", "name": "旧工作流", "models": [], "customNodes": [], "nodes": [], "targets": {}, "checks": {}}]
    s.dirty = True
    s.save()
    (s.outputs / "old.png").write_bytes(PNG)
    s.db.close()
    c, _ = make()
    with c:
        setup_admin(c)
        st = c.get("/api/state").json()
        assert [w["name"] for w in st["workflows"]] == ["旧工作流"]
        assert c.get("/api/files/old.png").status_code == 200
    assert not (legacy / "lumen.db").exists()
