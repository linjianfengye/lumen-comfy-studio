"""不开容器、不用 GPU 地查询 Modal：已部署应用的地址与配置、存储卷里的模型、保存部署配置。
由 ModalAdapter 用子进程调用（凭据走环境变量），输出一行 JSON。

    python modal_inspect.py app <app_name>          → {"url": ..., "config": {...}} 或 {"url": null}
    python modal_inspect.py volume <volume_name>    → {"files": ["checkpoints/a.safetensors", ...]}
    python modal_inspect.py save <app_name> <json>  → {"ok": true}
"""
import json
import sys

import modal


def app_info(name):
    try:
        fn = modal.Function.from_name(name, "ui")
        fn.hydrate()
        url = fn.get_web_url()
    except modal.exception.NotFoundError:
        return {"url": None}
    try:
        config = modal.Dict.from_name(f"{name}-meta").get("config")
    except modal.exception.NotFoundError:
        config = None
    return {"url": url, "config": config}


def volume_files(name):
    try:
        vol = modal.Volume.from_name(name)
        entries = vol.listdir("/", recursive=True)
    except modal.exception.NotFoundError:
        return {"files": []}
    files = [e.path.lstrip("/") for e in entries if e.type == modal.volume.FileEntryType.FILE]
    return {"files": [f for f in files if not f.endswith(".part")]}


def save(name, config):
    modal.Dict.from_name(f"{name}-meta", create_if_missing=True)["config"] = config
    return {"ok": True}


if __name__ == "__main__":
    cmd, name = sys.argv[1], sys.argv[2]
    out = app_info(name) if cmd == "app" else volume_files(name) if cmd == "volume" else save(name, json.loads(sys.argv[3]))
    print("LUMEN_JSON " + json.dumps(out))
