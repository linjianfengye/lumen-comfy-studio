"""python -m lumen  启动后端（默认只监听 127.0.0.1:8787）。"""
import sys

import uvicorn

from . import config as C
from .app import create_app

if __name__ == "__main__":
    if C.HOST not in ("127.0.0.1", "localhost", "::1") and not C.TOKEN:
        sys.exit("拒绝启动：监听非本机地址时必须设置 LUMEN_TOKEN（后端持有各平台密钥）")
    uvicorn.run(create_app(), host=C.HOST, port=C.PORT, log_level="info")
