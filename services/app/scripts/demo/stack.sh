#!/usr/bin/env bash
# 시연 네 장면 스택(D-412 · D-416 · D-418 · D-446 덧 10 · D-480 덧 5): stack.sh <real | copy | view> <명령> — 사람이 화면을 직접 누름.
#   up            스택 띄움(build 캐시 층만 · --pull never) → 정비자 · 파트장 화면 · 앱 → 배포 서버(real 8796 · copy DEPLOY_PORT)
#   deps          빌드 전 검사만: server pyproject · uv.lock · .python-version이 같은 로컬 앱 이미지가 있는가(없으면 멈춤)
#   info          컨테이너 안에서 배포 서버 /info 읽기(배포 id · Pack · revision — Run 아님)
#   pw            로그인 비밀번호 파일 경로만 알림(값은 그 파일에만)
#   alarm DEMO-01 시연 알람 하나(정비자가 로그인한 뒤에)
#   check         체크 기록 보내기 결과(기록 키 · 응답 코드 · 신호 id만)
#   backup <폴더> 반영 순서서 1: DB만 켜서 dump(gz) · 지금 이미지에 꼬리표(app_rev)
#   rebuild       반영 순서서 2: 같은 DB로 지금 트리 코드 다시 빌드(캐시 층만) · up
#   rollback <꼬리표> 반영 순서서 5: 꼬리표 이미지로 + ROLLBACK_TREE(그 커밋 트리) 화면 dist 복사
#   down          내리고 이 스택 자료 · 상태 파일 지움 — 시연 앱(real)에는 쓰지 않음(볼륨을 지움)
# 앱 DB를 처음으로 되돌리는 명령은 두지 않음(001-v (a): 체크 기록 키 CR-번호가 공유 기록에서 겹치지 않게 같은 앱 DB를 이어 씀).
# up은 보고서 번호를 월일시분부터 매김(첫 기록 키 예: CR-10271130). 저장소 .env는 읽지 않음. 설치 0 · VPS 0.
# 배포 서버(8796 · deploy 사본)는 deploy가 띄운 것 그대로(내리거나 고치지 않음).
set -euo pipefail
usage() { awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 1; }
[ $# -ge 1 ] || usage
. "$(dirname "$0")/stack-env.sh" "$1"
cd "$R"
load() { [ -f "$ST/stack.env" ] || { echo "스택 없음($ST) — 먼저 up"; exit 1; }; set -a; . "$ST/stack.env"; set +a; }
cache_check() {  # D-333: 빌드 RUN 층이 모두 캐시여야 함(설치 0)
  python3 - "$1" <<'PY' || { echo "build RUN이 캐시 밖 — 멈춤(D-333)"; exit 1; }
import re, sys
log = open(sys.argv[1], encoding="utf-8", errors="replace").read()
runs = set(re.findall(r"^#(\d+) \[[^\]]*\] RUN ", log, re.M)); cached = set(re.findall(r"^#(\d+) CACHED$", log, re.M))
sys.exit(1 if len(runs) < 2 or runs - cached else 0)
PY
}
wait_health() {
  for _ in $(seq 1 90); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "앱이 안 뜸"; exit 1; }
}
deps_check() {  # 빌드 전(D-546 덧 38): 의존 세 파일이 같은 로컬 앱 이미지가 없으면 uv sync가 패키지를 내려받음 → 빌드 전에 멈춤(설치 0 · D-333)
  local want img got
  want=$(cat "$R/server/pyproject.toml" "$R/server/uv.lock" "$R/server/.python-version" | shasum -a 256 | cut -c1-64)
  for img in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^c1a15-[a-z0-9-]+-api:'); do
    got=$(docker run --rm --pull never --network none --entrypoint sh "$img" -c 'cat /app/pyproject.toml /app/uv.lock /app/.python-version' 2>/dev/null | shasum -a 256 | cut -c1-64)
    [ "$got" = "$want" ] && { echo "의존 같음: $img"; return 0; }
  done
  echo "빌드 전 멈춤: server pyproject · uv.lock이 같은 로컬 앱 이미지 없음 — 빌드하면 패키지를 내려받음(D-333 · 설치 0)"; exit 1
}
build_images() {
  deps_check
  npm run --silent build >/dev/null
  docker compose build --progress plain > "$ST/build.log" 2>&1
  cache_check "$ST/build.log"
}
case "${2:-}" in
  up)
    [ ! -f "$ST/stack.env" ] || { echo "이미 떠 있음 — 상태 $ST(같은 앱 DB를 이어 씀)"; exit 1; }
    for p in "$TECH_PORT" "$LEAD_PORT" "$DB_HOST_PORT"; do lsof -nP -iTCP:"$p" -sTCP:LISTEN -t >/dev/null 2>&1 && { echo "포트 $p 사용 중 — 멈춤"; exit 1; }; done
    curl -fsS -m 5 "http://127.0.0.1:$DP/info" >/dev/null || { echo "배포 서버 $DP이 응답하지 않음 — 멈춤"; exit 1; }
    mkdir -p "$ST"; chmod 700 "$ST"
    gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(12))'; }
    (umask 077; printf 'POSTGRES_PASSWORD=%s\nC1A15_TECH_PASSWORD=%s\nC1A15_LEAD_PASSWORD=%s\nC1A15_INTERNAL_TOKEN=%s\nAPP_MODE=fixture\nC1A15_ALARM=%s\nC1A15_R12_INBOX=\nC1A15_DEPLOY_URL=http://host.docker.internal:%s\nC1A15_DEPLOY_HOST=host.docker.internal\nC1A15_CHECKS=%s\n' "$(gen)" "$(gen)" "$(gen)" "$(gen)" "$ALARM_MODE" "$DP" "$CHECKS" > "$ST/stack.env")
    if [ -n "$KG_INBOX_DIR" ]; then  # view: 승인 commit 뒤 편지 한 통 = 이 폴더 파일 하나(deploy 감시가 kg_apply)
      mkdir -p "$KG_INBOX_DIR"; printf 'C1A15_KG_INBOX=/inbox/kg\nVIEW_KG_INBOX=%s\n' "$KG_INBOX_DIR" >> "$ST/stack.env"
    fi
    load
    build_images
    docker compose up -d --pull never --no-build >/dev/null 2>&1
    wait_health
    # 보고서 번호 시작 = 월일시분: 체크 기록 키 CR-<번호>가 맥 공유 기록의 앞 바퀴(CR-000001 등)와 겹치지 않게(001-v · 공유 UNIQUE record_key)
    BASE=${REPORT_START:-$(/bin/date +%m%d%H%M | sed 's/^0*//')}  # view = 2(R-0002부터 · 덧 37)
    docker compose exec -T db psql -U c1a15 -d c1a15 -q -c "alter table ops.reports alter column report_id restart with $BASE"
    echo "REPORT_BASE=$BASE" >> "$ST/stack.env"
    git -C "$R" rev-parse --short HEAD > "$ST/app_rev"
    echo "떴음 — 정비자 http://127.0.0.1:$TECH_PORT (tech.kim) · 파트장 http://127.0.0.1:$LEAD_PORT (lead.park) · 비밀번호: $0 $1 pw${KG_INBOX_DIR:+ · 받은 편지함 $KG_INBOX_DIR}"
    ;;
  deps) deps_check ;;
  info)
    load
    docker compose exec -T api python -c 'import json, os, urllib.request; d = json.load(urllib.request.urlopen(os.environ["C1A15_DEPLOY_URL"] + "/info", timeout=5)); p = d.get("pack") or {}; print(d.get("deployment_id"), p.get("tag"), (p.get("pack_revision") or "")[:15])'
    ;;
  pw) load; echo "비밀번호 파일(모드 600): $ST/stack.env — C1A15_TECH_PASSWORD(정비자) · C1A15_LEAD_PASSWORD(파트장) 줄" ;;
  alarm) load; docker compose exec -T api python -m c1a15.alarms "${3:?DEMO-01}" ;;
  check)  # 체크 기록 보내기 결과만(메모 · 값 출력 0): 감사 줄마다 기록 키 · 응답 코드 · 신호 id
    load
    docker compose exec -T db psql -U c1a15 -d c1a15 -tA -F ' ' -c "select action, detail->>'record_key', coalesce(detail->>'status', detail->>'error'), detail->'signals' from ops.audit_log where action in ('check_record_sent', 'check_record_failed') order by detail->>'record_key'"
    ;;
  backup)  # 반영 순서서 1: DB만 켜서 dump · 지금 이미지에 꼬리표(app_rev) — 띄운 앱 손 0
    load; B=${3:?백업 폴더}; mkdir -p "$B"
    docker compose start db >/dev/null 2>&1
    for _ in $(seq 1 30); do docker compose exec -T db pg_isready -U c1a15 -d c1a15 >/dev/null 2>&1 && break; sleep 1; done
    T=$(/bin/date +%Y%m%d-%H%M%S); F="$B/$COMPOSE_PROJECT_NAME-$T.sql.gz"
    docker compose exec -T db pg_dump -U c1a15 -d c1a15 | gzip > "$F"
    [ "$(gzip -dc "$F" | grep -c 'ops.session_events')" -gt 0 ] || { echo "백업이 비었음 — 멈춤"; exit 1; }
    REV=$(cat "$ST/app_rev" 2>/dev/null || echo old)
    for s in api init; do docker tag "$COMPOSE_PROJECT_NAME-$s" "$COMPOSE_PROJECT_NAME-$s:$REV"; done
    echo "백업 $F · 이미지 꼬리표 :$REV"
    ;;
  rebuild)  # 반영 순서서 2: 같은 DB · 같은 stack.env로 지금 트리 코드 이미지(캐시 층만) → up(--no-build) · init은 설치된 DB면 0
    load
    build_images
    docker compose up -d --pull never --no-build >/dev/null 2>&1
    wait_health
    git -C "$R" rev-parse --short HEAD > "$ST/app_rev"
    echo "다시 빌드 $(cat "$ST/app_rev") · health 200"
    ;;
  rollback)  # 반영 순서서 5: 꼬리표 이미지로 · 화면은 그 커밋 트리에서 빌드해 이 트리 dist로 복사(web은 $R/apps/*/dist를 읽음)
    load; REV=${3:?꼬리표(예 88e0d06)}; RT=${ROLLBACK_TREE:?ROLLBACK_TREE = 그 커밋의 작업 트리}
    [ "$(git -C "$RT" rev-parse --short HEAD 2>/dev/null)" = "$REV" ] || { echo "ROLLBACK_TREE가 $REV 아님 — 멈춤"; exit 1; }
    for s in api init; do docker image inspect "$COMPOSE_PROJECT_NAME-$s:$REV" >/dev/null 2>&1 || { echo "꼬리표 이미지 $COMPOSE_PROJECT_NAME-$s:$REV 없음 — 멈춤"; exit 1; }; done
    for s in api init; do docker tag "$COMPOSE_PROJECT_NAME-$s:$REV" "$COMPOSE_PROJECT_NAME-$s"; done
    (cd "$RT" && npm run --silent build >/dev/null)
    for a in tech lead; do rsync -a --delete "$RT/apps/$a/dist/" "$R/apps/$a/dist/"; done
    docker compose up -d --pull never --no-build --force-recreate >/dev/null 2>&1
    wait_health
    echo "$REV" > "$ST/app_rev"
    echo "되돌림 :$REV · health 200"
    ;;
  down) [ -f "$ST/stack.env" ] && load; docker compose down -v --remove-orphans >/dev/null 2>&1 || true; rm -rf "${ST:?}"; echo "내림 · 지움" ;;
  *) usage ;;
esac
