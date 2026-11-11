# [dev-2] 시연 대본 공통 자리(값 0 · 경로만) — 이 파일 자리 기준으로 셈. 시연은 repo/worktrees/<시연 worktree>에서 돎(D-575).
# 바꾸려면 환경 변수로: DEMO_REPO(repo 뿌리) · DEMO_VAR(시연 자료) · DEMO_W(시연 worktree) · DEMO_STATE(앱 시연 상태 폴더) · DEMO_KEY_FILE(키 파일 경로)
_src="${BASH_SOURCE[0]:-${(%):-%x}}"   # bash · zsh 둘 다(source한 파일 자리)
_here="$(cd "$(dirname "$_src")" && pwd)"
export DEMO_W="${DEMO_W:-$(cd "$_here/../.." && pwd)}"
export DEMO_REPO="${DEMO_REPO:-$(cd "$DEMO_W/../.." && pwd)}"
export DEMO_VAR="${DEMO_VAR:-$DEMO_REPO/llmopsagent-deploy/var/c1a15-dev2-demo}"
export DEMO_STATE="${DEMO_STATE:-$DEMO_REPO/worktrees/c1a15-dev2-demo-state}"
export DEMO_KEY_FILE="${DEMO_KEY_FILE:-$DEMO_REPO/c1a15-pack/.env}"
export DEMO_PY="${DEMO_PY:-$DEMO_REPO/llmopsagent-deploy/.venv/bin/python}"
