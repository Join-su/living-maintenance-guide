#!/bin/sh
# 개발 서버(127.0.0.1만). 설정: DEPLOY_CONFIG(기본 config/dev-local.yaml), 포트: DEPLOY_PORT(기본 8780)
# onnxruntime telemetry 끔(D-311 · D-315): 맥 .venv의 onnxruntime(KS-3)은 이 값이 없으면 Microsoft로 사용 기록을 보냄 — packrt 0.12.1도 끄지만 서비스 env에도 둠
export DEPLOY_CONFIG="${DEPLOY_CONFIG:-config/dev-local.yaml}"
export ORT_DISABLE_TELEMETRY=1
exec "$(dirname "$0")/py" -m uvicorn --factory deploy_server.app:create_app_from_env --host 127.0.0.1 --port "${DEPLOY_PORT:-8780}"
