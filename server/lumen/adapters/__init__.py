"""平台适配器。每个已连接的平台对应一个实例，调度器只通过这里的接口和平台打交道。"""
from .base import Adapter, JobSpec, AdapterError
from .demo import DemoAdapter
from .local import LocalAdapter
from .modal_app import ModalAdapter
from .runpod import RunPodAdapter
from .vast import VastAdapter

REAL = {"local": LocalAdapter, "modal": ModalAdapter, "runpod": RunPodAdapter, "vast": VastAdapter}


def make_adapter(pid: str, provider: dict) -> Adapter:
    if provider.get("demo"):
        return DemoAdapter(pid, provider)
    cls = REAL.get(pid)
    if not cls:
        raise AdapterError("该平台的真实接入还在开发中，请先用演示模式体验")
    return cls(pid, provider)


__all__ = ["Adapter", "JobSpec", "AdapterError", "make_adapter", "REAL"]
