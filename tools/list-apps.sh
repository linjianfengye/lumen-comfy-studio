#!/usr/bin/env bash
# 列出当前发布者在 BigApple 上的应用（只读）
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.claude/skills/bigapple-deploy/scripts" && pwd)"
source "$SKILL_DIR/deployment-auth.sh"
API_BASE="${BIGAPPLE_DEPLOY_API_BASE:-https://api.bigapple.work}"
initialize_deployment_auth "$API_BASE" "${CLAUDE_GUI_DATA_DIR:-$HOME/.bigapple}" "${BIGAPPLE_DEPLOY_PUBLISHER_ID:-}"
curl -sS --connect-timeout 10 --max-time 60 \
  -H "x-bigapple-publisher-id: $PUBLISHER_ID" -H "Authorization: Bearer $DEPLOYMENT_TOKEN" \
  -H "x-bigapple-skill-id: $SKILL_ID" -H "x-bigapple-skill-version: $SKILL_VERSION" \
  "$API_BASE/api/apps" | "$DEPLOYMENT_PYTHON_BIN" -c "
import json,sys
d=json.load(sys.stdin)
for a in d.get('apps', d if isinstance(d,list) else []):
    print(a.get('appId'), a.get('slug'), a.get('runtime'), a.get('publicUrl'), a.get('publisherId'))
"
