"""演示适配器：不连任何平台，按真实流程的节奏模拟部署、开机、出图。作品用示例图占位。"""
import asyncio
import random

from .base import Adapter

SAMPLES = ["lake", "perfume", "portrait", "city", "tea", "dune"]
STEPS = {
    "serverless": ["构建 ComfyUI 镜像", "创建模型存储卷", "安装常用自定义节点", "发布 HTTP 接口", "健康检查"],
    "instance": ["创建实例", "拉取 ComfyUI 镜像", "挂载数据盘", "安装常用自定义节点", "健康检查后关机待命"],
    "local": ["探测 /system_stats", "读取节点列表"],
}


async def _steps(steps, seconds, progress):
    for i, name in enumerate(steps):
        progress(i / len(steps), name)
        await asyncio.sleep(seconds / len(steps))
    progress(1, steps[-1])


class DemoAdapter(Adapter):
    speed = 1.0    # 测试里调小以加速

    async def deploy(self, progress):
        await _steps(STEPS[self.lifecycle], 14 * self.speed, progress)
        return {"info": {"device": f"{self.gpu}（演示）", "version": "demo"}}

    async def start(self):
        await asyncio.sleep(self.meta["coldStart"] * self.speed)

    async def deploy_model(self, model, progress):
        # 演示：1.2 GB/s 拉到平台存储卷
        size = model.get("size") or 2
        done = 0.0
        while done < size:
            await asyncio.sleep(0.5 * self.speed)
            done += 0.6
            progress(min(1, done / size))

    async def run_job(self, spec, progress, cancelled):
        total = (6 * ((spec.frames or 65) / 16) if spec.kind == "video" else 7) * random.uniform(0.85, 1.15)
        if any(k in (self.gpu or "") for k in ("H100", "A100", "A800", "L40S", "4090")):
            total *= 0.6
        t = 0.0
        while t < total:
            if cancelled():
                raise asyncio.CancelledError()
            await asyncio.sleep(0.5 * self.speed)
            t += 0.5
            progress(min(1, t / total))
        return {"src": f"/samples/{random.choice(SAMPLES)}.webp", "seconds": total}
