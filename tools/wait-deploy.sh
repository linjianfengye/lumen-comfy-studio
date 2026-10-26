#!/usr/bin/env bash
# 轮询 BigApple 上 lumen 的部署状态，直到不再是 running（只读，最多 40 分钟）
set -uo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.claude/skills/bigapple-deploy/scripts" && pwd)"
source "$SKILL_DIR/deployment-auth.sh"
API_BASE="${BIGAPPLE_DEPLOY_API_BASE:-https://api.bigapple.work}"
initialize_deployment_auth "$API_BASE" "${CLAUDE_GUI_DATA_DIR:-$HOME/.bigapple}" "${BIGAPPLE_DEPLOY_PUBLISHER_ID:-}"
for _ in $(seq 1 80); do
  line="$(curl -sS --max-time 30 -H "x-bigapple-publisher-id: $PUBLISHER_ID" -H "Authorization: Bearer $DEPLOYMENT_TOKEN" \
    -H "x-bigapple-skill-id: $SKILL_ID" -H "x-bigapple-skill-version: $SKILL_VERSION" "$API_BASE/api/apps/${1:-lumen}" \
    | "$DEPLOYMENT_PYTHON_BIN" -c "
import json,sys
d=json.load(sys.stdin); x=d['deployments'][0]
print(x['status'], x['updatedAt'], x['errorMessage'] or '', d['app']['currentDeploymentId'] or '')" 2>&1)"
  echo "$(date -u +%H:%M:%S) $line"
  case "$line" in running*|"") sleep 30 ;; *) break ;; esac
done
curl -s -o /dev/null -w 'health: %{http_code}\n' --max-time 20 "https://${1:-lumen}.bigapple.work/api/health"
