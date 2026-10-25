"""路径与运行参数。全部可用环境变量覆盖。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # comfy-studio/
SHARED = ROOT / "shared"
DATA = Path(os.environ.get("LUMEN_DATA", ROOT / "server" / "data"))
OUTPUTS = DATA / "outputs"
UPLOADS = DATA / "uploads"
DEPLOY = DATA / "deploy"
DB_PATH = DATA / "lumen.db"
KEY_PATH = DATA / "secret.key"
DIST = ROOT / "dist"                                # npm run build 的产物，由后端直接托管

HOST = os.environ.get("LUMEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUMEN_PORT", "8787"))
# 设置后所有 /api 请求都要带 Authorization: Bearer <token>；对局域网 / 公网开放时必须设置
TOKEN = os.environ.get("LUMEN_TOKEN", "")

USD_TO_CNY = float(os.environ.get("LUMEN_USD_CNY", "7.2"))
DEMO_TIME_SCALE = 10      # 演示平台的空闲倒计时倍速
TICK_SECONDS = 1.0

for d in (DATA, OUTPUTS, UPLOADS, DEPLOY):
    d.mkdir(parents=True, exist_ok=True)

PROVIDERS = json.loads((SHARED / "providers.json").read_text(encoding="utf-8"))
PROVIDER_BY_ID = {p["id"]: p for p in PROVIDERS}
SAMPLES = json.loads((SHARED / "samples.json").read_text(encoding="utf-8"))


def price_of(provider_id: str, gpu: str) -> float:
    p = PROVIDER_BY_ID.get(provider_id)
    return next((g["price"] for g in (p or {}).get("gpus", []) if g["id"] == gpu), 0.0)
