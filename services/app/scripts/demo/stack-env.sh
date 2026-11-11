# 시연 스택 이름별 칸(stack.sh · reload-manual.sh가 source) — 맥 절대 경로 0: 저장소 뿌리는 이 파일 자리로 셈.
#   real = 시연 앱(c1a15-demo-real · 정비자 8081 · 파트장 8082 · DB 55437 · 맥 배포 서버 8796 · 이 저장소 트리)
#   copy = 사본(c1a15-demo-copy · 18181 · 18182 · DB 55440 · deploy 사본 DEPLOY_PORT(기본 18796) · APP_TREE(기본 이 트리))
#   view = dev-2 보기 사본(D-546 F2 · c1a15-dev2-view · 28181 · 28182 · DB 55453 · 배포 DEPLOY_PORT(기본 28898)
#          · 알람 ML만 · 그래프 받은 편지함 = 상태 폴더 kg-inbox → 컨테이너 /inbox/kg)
DEMO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$DEMO_DIR/../.." && pwd)
case "${1:-}" in
  real)
    R=$REPO_ROOT; DP=8796; TECH_PORT=8081; LEAD_PORT=8082
    export COMPOSE_PROJECT_NAME=c1a15-demo-real COMPOSE_FILE=docker-compose.yml DB_HOST_PORT=55437 ;;
  copy)
    R=${APP_TREE:-$REPO_ROOT}; DP=${DEPLOY_PORT:-18796}; TECH_PORT=18181; LEAD_PORT=18182  # D-465: APP_TREE = 빌드할 작업 트리
    export COMPOSE_PROJECT_NAME=c1a15-demo-copy COMPOSE_FILE="docker-compose.yml:$DEMO_DIR/ports-copy.yml" DB_HOST_PORT=55440 ;;
  view)
    R=${APP_TREE:-$REPO_ROOT}; DP=${DEPLOY_PORT:-28898}; TECH_PORT=28181; LEAD_PORT=28182
    export COMPOSE_PROJECT_NAME=c1a15-dev2-view COMPOSE_FILE="docker-compose.yml:$DEMO_DIR/ports-view.yml" DB_HOST_PORT=55453 ;;
  *) echo "스택 이름은 real | copy | view"; exit 1 ;;
esac
ST=${TMPDIR:-/tmp}/$COMPOSE_PROJECT_NAME
ALARM_MODE=boundary; KG_INBOX_DIR=; REPORT_START=; CHECKS=deploy  # real · copy = 시연 길 그대로(경계 · 받은 편지함 0 · 번호 월일시분)
# view: 승인 이력 번호 = 시작 그래프 예외 기록(R-0001) 뒤 R-0002부터(D-546 덧 37) · 사람 체크 보내기 끔(첫 상태로 되돌린 뒤 기록 키 겹침 0)
if [ "$1" = view ]; then ALARM_MODE=ml; KG_INBOX_DIR=$ST/kg-inbox; REPORT_START=${VIEW_REPORT_START:-2}; CHECKS=off; fi
PORT=$TECH_PORT
