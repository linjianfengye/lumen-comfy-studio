import io
import json

from fastapi.testclient import TestClient

from lumen import config as C
from lumen.app import create_app
from lumen.store import Store


def client(tmp_path, token=""):
    C.TOKEN = token
    return TestClient(create_app(Store(tmp_path / "a.db"), start_engine=False))


def test_state_and_workflow_import(tmp_path):
    with client(tmp_path) as c:
        s = c.get("/api/state").json()
        assert "providers" in s and len(s["gallery"]) == 6
        r = c.post("/api/workflows", files={"file": ("sdxl.json", io.BytesIO(json.dumps(C.SAMPLES["sdxl"]).encode()), "application/json")})
        assert r.status_code == 200 and r.json()["models"] == 3
        bad = c.post("/api/workflows", files={"file": ("x.json", io.BytesIO(b'{"a":1}'), "application/json")})
        assert bad.status_code == 400 and "无法识别" in bad.json()["detail"]
        # 原始 JSON 不进快照
        assert "raw" not in c.get("/api/state").json()["workflows"][0]


def test_connect_errors_are_400(tmp_path):
    with client(tmp_path) as c:
        r = c.post("/api/providers/fal/connect", json={"config": {}, "demo": False})
        assert r.status_code == 400 and "演示模式" in r.json()["detail"]
        assert c.post("/api/providers/fal/connect", json={"config": {}, "demo": True}).status_code == 200


def test_file_route_rejects_traversal(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/files/..%2Fsecret.key").status_code == 404
        assert c.get("/api/files/nope.png").status_code == 404


def test_token_auth(tmp_path):
    with client(tmp_path, token="t0k") as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/state").status_code == 401
        assert c.post("/api/login", json={"token": "bad"}).status_code == 401
        assert c.post("/api/login", json={"token": "t0k"}).status_code == 200
        assert c.get("/api/state").status_code == 200       # Cookie 生效
    C.TOKEN = ""
