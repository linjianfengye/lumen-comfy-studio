"""用演示适配器跑一遍完整生命周期：连接 → 部署 ComfyUI → 导入工作流 → 部署模型 → 批量出图 → 空闲关机。"""
import asyncio

import pytest

from lumen import config as C
from lumen.adapters.demo import DemoAdapter
from lumen.engine import ActionError, Engine
from lumen.store import Store


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(DemoAdapter, "speed", 0.002)
    return Engine(Store(tmp_path / "t.db"))


async def until(engine, cond, dt=1.0, steps=2000):
    for _ in range(steps):
        engine.tick(dt)
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("条件一直没满足")


def test_demo_lifecycle(engine):
    async def go():
        s = engine.s
        engine.set_policy({"idleMinutes": 1})
        engine.connect("modal", {}, demo=True)
        engine.deploy_comfy(["modal"])
        await until(engine, lambda: s["providers"]["modal"]["comfy"]["status"] == "ready")

        wf = engine.import_workflow(C.SAMPLES["sdxl"], "sdxl.json")   # 自动部署模型
        await until(engine, lambda: engine.ready_on(wf, "modal"))
        assert set(s["modelCache"]["modal"]) >= {m["name"] for m in wf["models"]}

        before = len(s["gallery"])
        b = engine.create_batch({"workflowId": wf["id"], "prompts": ["a", "b"], "perPrompt": 1, "seedMode": "step",
                                 "seedStart": 10, "kind": "image", "size": "1024x1024", "providerIds": ["modal"]})
        await until(engine, lambda: s["runtime"]["modal"]["power"] in ("starting", "running"))
        await until(engine, lambda: engine.store.batch(b["id"])["status"] == "done")
        assert len(s["gallery"]) == before + 2
        assert [j["seed"] for j in engine.store.batch(b["id"])["jobs"]] == [10, 11]
        assert s["spend"]["today"] > 0

        # 空闲 1 分钟（演示 10 倍速）后自动关机
        await until(engine, lambda: s["runtime"]["modal"]["power"] == "off", dt=1.0)

    asyncio.run(go())


def test_budget_blocks_autostart(engine):
    async def go():
        engine.connect("runpod", {}, demo=True)
        engine.deploy_comfy(["runpod"])
        await until(engine, lambda: engine.comfy_ready("runpod"))
        engine.s["spend"]["today"] = 99
        engine.create_batch({"prompts": ["x"], "perPrompt": 1, "providerIds": ["runpod"]})
        for _ in range(20):
            engine.tick(1)
            await asyncio.sleep(0.005)
        assert engine.s["runtime"]["runpod"]["power"] == "off"

    asyncio.run(go())


def test_real_connect_rules(engine):
    with pytest.raises(ActionError, match="开发中"):
        engine.connect("fal", {"apiKey": "x"}, demo=False)
    with pytest.raises(ActionError, match="请填写"):
        engine.connect("vast", {"apiKey": "k"}, demo=False)
    with pytest.raises(ActionError, match="已部署 ComfyUI"):
        engine.create_batch({"prompts": ["x"], "providerIds": ["modal"]})


def test_secrets_encrypted_and_masked(engine, tmp_path):
    engine.connect("vast", {"apiKey": "SECRET-123", "instanceId": "42", "uiUrl": "http://1.2.3.4:8188"}, demo=False)
    engine.store.save()
    raw = (tmp_path / "t.db").read_bytes()
    assert b"SECRET-123" not in raw
    snap = engine.store.snapshot()
    assert snap["providers"]["vast"]["config"]["apiKey"] == "••••••"
    assert "apiKey" in snap["providers"]["vast"]["secretsSet"]
    # 重新加载能解密
    assert Store(tmp_path / "t.db").state["providers"]["vast"]["config"]["apiKey"] == "SECRET-123"
    # 前端回传掩码时保留原值
    engine.connect("vast", {"apiKey": "••••••", "instanceId": "42", "uiUrl": "http://1.2.3.4:8188"}, demo=False)
    assert engine.store.provider("vast")["config"]["apiKey"] == "SECRET-123"
