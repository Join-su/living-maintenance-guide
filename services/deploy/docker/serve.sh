#!/bin/sh
# Container copy of scripts/serve.sh (the image puts it in its place). Same start; only the address comes from DEPLOY_HOST
# (pack-server-start sets 0.0.0.0 so other containers reach pack-server:8796 · outside, compose opens it on 127.0.0.1 only).
# Settings: DEPLOY_CONFIG · port: DEPLOY_PORT. ORT_DISABLE_TELEMETRY=1 keeps onnxruntime from sending usage data.
export DEPLOY_CONFIG="${DEPLOY_CONFIG:-config/dev-local.yaml}"
export ORT_DISABLE_TELEMETRY=1
exec "$(dirname "$0")/py" -m uvicorn --factory deploy_server.app:create_app_from_env --host "${DEPLOY_HOST:-127.0.0.1}" --port "${DEPLOY_PORT:-8780}"
