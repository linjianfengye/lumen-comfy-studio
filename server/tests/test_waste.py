"""防浪费：沿用已有 Modal 部署、存储卷已有模型不下载、部署后不冷启动、Vast 关机时推迟核对。"""
import asyncio

import pytest

from lumen import config as C
from lumen.adapters.modal_app import COMFY_VERSION, ModalAdapter
from lumen.adapters.vast import VastAdapter
from lumen.engine import Engine
from lumen.store import Store


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DEPLOY", tmp_path)
    return Engine(Store(tmp_path / "t.db"))


def modal_engine(engine, monkeypatch, have=None, files=()):
    calls = {"deploy": 0, "download": [], "probe": 0, "saved": None}

    async def inspect(self, cmd, name, *rest, timeout=120):
        if cmd == "app":
            return have or {"url": None}
        if cmd == "volume":
            return {"files": list(files)}
        calls["saved"] = rest[0]
        return {"ok": True}

    async def run(self, args, on_line, timeout=3600):
        if args[0] == "deploy":
            calls["deploy"] += 1
            on_line("✓ Created web function ui => https://me--lumen-comfyui-ui.modal.run")
        else:
            calls["download"].append(args[args.index("--filename") + 1])
        return 0, ""

    async def probe(*a, **k):
        calls["probe"] += 1
        return {"device": "GPU"}

    monkeypatch.setattr(ModalAdapter, "_inspect", inspect)
    monkeypatch.setattr(ModalAdapter, "_run", run)
    monkeypatch.setattr("lumen.comfy.ComfyClient.probe", probe)
    engine.connect("modal", {"tokenId": "ak-x", "tokenSecret": "as-y"}, demo=False)
    return calls


async def settle(engine, cond, steps=400):
    for _ in range(steps):
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("条件一直没满足")


def test_covers():
    want = {"gpu": "A10G", "nodePacks": ["a"], "scaledownMinutes": 5, "comfyVersion": COMFY_VERSION}
    assert ModalAdapter.covers({**want, "nodePacks": ["a", "b"]}, want)
    assert not ModalAdapter.covers({**want, "nodePacks": []}, want)
    assert not ModalAdapter.covers({**want, "gpu": "T4"}, want)
    assert not ModalAdapter.covers(None, want)


def test_reuse_existing_modal_deploy(engine, monkeypatch):
    gpu = engine.store.provider("modal")["gpu"]
    have = {"url": "https://me--lumen-comfyui-ui.modal.run",
            "config": {"gpu": gpu, "nodePacks": [], "scaledownMinutes": 5, "comfyVersion": COMFY_VERSION}}
    calls = modal_engine(engine, monkeypatch, have=have)

    async def go():
        engine.deploy_comfy(["modal"])
        await settle(engine, lambda: engine.comfy_ready("modal"))
    asyncio.run(go())
    c = engine.store.provider("modal")["comfy"]
    assert calls["deploy"] == 0 and calls["probe"] == 0
    assert c["reused"] and c["url"] == have["url"]


def test_fresh_deploy_no_coldstart_and_volume_skip(engine, monkeypatch):
    wf_raw = C.SAMPLES["sdxl"]
    from lumen.workflow import parse
    models = parse(wf_raw, "x.json")["models"]
    first = models[0]
    for m in models:
        m.setdefault("source", "https://example.com/m")
    calls = modal_engine(engine, monkeypatch, files=[f"{first['dir']}/{first['name'].split('/')[-1]}"])

    async def go():
        engine.deploy_comfy(["modal"])
        await settle(engine, lambda: engine.comfy_ready("modal"))
        wf = engine.import_workflow(wf_raw, "x.json")
        for m in wf["models"]:
            m["source"] = "https://example.com/m"
        engine.deploy_workflow(wf["id"], ["modal"])
        await settle(engine, lambda: wf["targets"].get("modal", {}).get("status") in ("ready", "error"))
        # 再部署一次：节点包和模型都已就绪，不应再部署镜像或下载
        assert wf["targets"]["modal"]["status"] == "ready"
        n_dl = len(calls["download"])
        engine.deploy_workflow(wf["id"], ["modal"])
        await asyncio.sleep(0.05)
        assert len(calls["download"]) == n_dl
        return wf
    wf = asyncio.run(go())
    # 首次部署 + 工作流带来新节点包（impact-pack）重新部署一次，之后不再重复
    assert calls["deploy"] == 2 and calls["probe"] == 0      # 部署后不冷启动
    assert calls["saved"]                                   # 部署配置已保存，下次可沿用
    assert first["name"].split("/")[-1] not in calls["download"]   # 存储卷已有的不再下载
    assert len(calls["download"]) == len(models) - 1


def test_vast_check_deferred_until_power_on(engine, monkeypatch):
    checked = []

    async def deploy(self, progress):
        return {"url": "http://vast", "info": {}}

    async def check(self, wf, raw):
        checked.append(wf["id"])
        return {"status": "ready", "missingModels": [], "missingNodes": []}

    async def start(self):
        pass

    monkeypatch.setattr(VastAdapter, "deploy", deploy)
    monkeypatch.setattr(VastAdapter, "check_models", check)
    monkeypatch.setattr(VastAdapter, "start", start)

    async def go():
        engine.connect("vast", {"apiKey": "k", "instanceId": "1", "uiUrl": "http://vast"}, demo=False)
        engine.deploy_comfy(["vast"])
        await settle(engine, lambda: engine.comfy_ready("vast"))
        wf = engine.import_workflow(C.SAMPLES["sdxl"], "x.json")
        await asyncio.sleep(0.02)
        assert wf["checks"]["vast"]["status"] == "pending" and not checked   # 关机时不开机核对
        engine.start_provider("vast")
        await settle(engine, lambda: wf["checks"]["vast"]["status"] == "ready")
        assert checked == [wf["id"]]
    asyncio.run(go())
