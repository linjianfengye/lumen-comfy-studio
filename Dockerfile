# Lumen：FastAPI 后端 + 已构建的前端（dist/ 由 `npm run build` 生成，随包上传）
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY server/requirements.txt server/requirements.txt
# 运行时不需要 pytest
RUN grep -v '^pytest' server/requirements.txt > /tmp/req.txt \
    && pip install --retries 8 --timeout 60 -r /tmp/req.txt

COPY shared shared
COPY server/lumen server/lumen
COPY dist dist

WORKDIR /app/server
# 平台注入 PORT 与 BIGAPPLE_DATA_DIR；LUMEN_TOKEN 通过 Secrets 注入，未设置时后端拒绝启动
CMD ["sh", "-c", "LUMEN_HOST=0.0.0.0 LUMEN_PORT=${PORT:-8787} LUMEN_DATA=${BIGAPPLE_DATA_DIR:-/data} exec python -m lumen"]
