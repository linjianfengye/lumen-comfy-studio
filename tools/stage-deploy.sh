#!/usr/bin/env bash
# 构建前端并把需要上线的文件复制到临时目录（不含 .venv、server/data 里的密钥与数据库、设计稿）
# 用法：bash tools/stage-deploy.sh   → 输出临时目录路径
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
npm run build >/dev/null
OUT="$(mktemp -d "${TMPDIR:-/tmp}/lumen-stage.XXXXXX")"
mkdir -p "$OUT/server"
cp Dockerfile "$OUT/"
cp -r shared dist "$OUT/"
cp server/requirements.txt "$OUT/server/"
cp -r server/lumen "$OUT/server/"
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "$OUT"
