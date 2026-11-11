#!/bin/bash
# [dev-2] 8796을 시연 worktree 설정 그대로 다시 띄움(그 pid만 · cwd 확인) — AI 키 경로 · 주소는 경로만 환경으로. set-tag.sh · provider.sh가 부름.
set -eo pipefail
. "$(cd "$(dirname "$0")" && pwd)/common.sh"; cd "$DEMO_W"
scripts/py scripts/r12-update check --config config/c1a15-dev2-demo.yaml | tail -1
PID=$(lsof -nP -t -iTCP:8796 -sTCP:LISTEN | head -1); C=$(lsof -a -p "$PID" -d cwd -Fn | grep '^n' | cut -c2-)
[ "$C" = "$DEMO_W" ] || { echo "멈춤: 8796 pid $PID가 시연 worktree에서 돌지 않음($C)"; exit 1; }
t0=$(date +%s); kill "$PID"; for i in $(seq 1 40); do lsof -nP -iTCP:8796 -sTCP:LISTEN >/dev/null || break; sleep 0.25; done
(DEPLOY_VENV="$DEMO_REPO/llmopsagent-deploy/.venv" DEPLOY_CONFIG=config/c1a15-dev2-demo.yaml DEPLOY_PORT=8796 PACKRT_ENV_FILE="$DEMO_KEY_FILE" OPENAI_BASE_URL=https://api.openai.com/v1 \
  nohup scripts/serve.sh >> "$DEMO_VAR/logs/serve-8796-demo.log" 2>&1 &)
for i in $(seq 1 120); do curl -sf 127.0.0.1:8796/health >/dev/null && break; sleep 0.5; done
echo "끊김 $(( $(date +%s) - t0 ))초 · /info $(curl -s 127.0.0.1:8796/info | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["pack"].get("tag"), d["pack"]["pack_revision"][7:15], d["provider"]["mode"])')"
