#!/bin/sh
# app-init — 앱 DB 첫 상태(메인 시나리오 시작 전) · api 이미지 안에서 한 번 돌고 끝남. 값 출력 0.
#
#   app-init.sh lines     (kg-init 앞) 빈 DB면 migration · 적재(c1a15.dbinit · 있으면 판 대조만) → M-04 담당 = 김 정비
#                         → 시연 이력 줄 6개를 앱 DB에 넣고 같은 줄을 /srv/seed-lines/line-M-01 · 02 · 03 · 13.jsonl로(kg-init이 읽음)
#   app-init.sh examples  (kg-init 뒤) kg-init이 만든 예시 F 8줄(/srv/pack-repo/examples-F.jsonl)을 앱 이력 DB에 넣음(지난 예시 줄은 빼고 다시)
#
# 기준 시각 AT: 처음 돌 때 정해 /srv/seed-lines/at.env에 남기고 그 뒤로는 그 값만 씀(다시 돌려도 같은 줄 · 같은 번호는 바꿔 넣음).
#   AT 환경 값이 있으면 그 값(꼴 2026-11-11T09:00:00+09:00) · 없으면 처음 돌 때의 지금(KST · 분 아래 버림).
#   kg-init도 같은 AT여야 그래프 · 앱 이력의 날짜가 맞음 — examples 단계가 first-state.env의 AT와 대조하고 다르면 멈춤.
# 끝 코드: 0 끝 · 2 입력 틀림(AT 꼴 · 남은 AT와 다름) · 3 DB 준비 실패 · 4 시연 줄 실패 · 5 예시 F 없음/AT 다름 · 6 예시 넣기 실패
set -eu
MODE=${1:-}
LINES=${LINES_DIR:-/srv/seed-lines}; LIVE=${PACK_LIVE:-/srv/pack-repo}
say() { echo "$(date '+%H:%M:%S') app-init $MODE · $*"; }
stop() { echo "APP_INIT stop($1) · $2"; exit "$1"; }
at_ok() { echo "$1" | grep -Eq '^20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]\+09:00$'; }
saved_at() { [ -f "$LINES/at.env" ] && sed -n 's/^AT=//p' "$LINES/at.env" | tail -1 || true; }

case "$MODE" in
lines)
  OLD=$(saved_at)
  if [ -n "$OLD" ]; then
    [ -z "${AT:-}" ] || [ "$AT" = "$OLD" ] || stop 2 "AT($AT)가 처음 정한 값($OLD)과 다름 — 첫 상태를 새로 만들려면 볼륨을 비우고 다시"
    AT=$OLD
  else
    AT=${AT:-$(python -c 'import datetime as d; print(d.datetime.now(d.timezone(d.timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:00+09:00"))')}   # tzdata 없어도 KST
  fi
  at_ok "$AT" || stop 2 "AT 꼴이 다름: $AT"
  python -m c1a15.dbinit || stop 3 "dbinit 실패"
  python - <<'PY' || stop 3 "M-04 담당 바꾸기 실패"
from sqlalchemy import text
from c1a15.api import deps
with deps.engine().begin() as c:
    n = c.execute(text("update ops.equipment_assignments set user_id = 'tech.kim' where equipment_id = 'M-04'")).rowcount
print(f"M-04 담당 = tech.kim ({n}줄)")
PY
  W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
  X="python -m c1a15.demo_history add"
  # 메인 시나리오: M-01 3일 전 4.3.2(H-0301) · M-03 2일 전 4.3.2(H-0302) · M-13 지난 4.3 · 4.6 · M-02 지난 CL-4 둘 — 시연 첫 상태와 같은 인자
  $X --at "$AT" > "$W/line-M-01.jsonl" || stop 4 "M-01 줄"
  $X --record H-0302 --equipment M-03 --group CL --days-ago 2 --action 4.3.2 --at "$AT" > "$W/line-M-03.jsonl" || stop 4 "M-03 줄"
  $X --record H-0303 --equipment M-13 --group CL --at 2026-11-07T10:00:00+09:00 --days-ago 60 --check 4.3 --action 4.3.2 > "$W/line-M-13.jsonl" || stop 4 "M-13 줄 1"
  $X --record H-0304 --equipment M-13 --group CL --days-ago 20 --check 4.6 --action 4.6.2 --at "$AT" >> "$W/line-M-13.jsonl" || stop 4 "M-13 줄 2"
  $X --record H-0305 --equipment M-02 --group CL --days-ago 30 --check 4.6 --action 4.6.2 --at "$AT" > "$W/line-M-02.jsonl" || stop 4 "M-02 줄 1"
  $X --record H-0306 --equipment M-02 --group CL --days-ago 37 --check 4.6 --action 4.6.2 --at "$AT" >> "$W/line-M-02.jsonl" || stop 4 "M-02 줄 2"
  [ "$(cat "$W"/line-*.jsonl | grep -c .)" = 6 ] || stop 4 "줄 6이 아님"
  mkdir -p "$LINES"
  for f in "$W"/line-*.jsonl; do cp "$f" "$LINES/.$(basename "$f").new" && mv "$LINES/.$(basename "$f").new" "$LINES/$(basename "$f")"; done
  [ -n "$OLD" ] || printf 'AT=%s\n' "$AT" > "$LINES/at.env"
  echo "APP_INIT lines ok AT=$AT FILES=4 LINES=6"
  ;;
examples)
  AT=$(saved_at); [ -n "$AT" ] || stop 5 "$LINES/at.env 없음 — lines 단계 먼저"
  F=$LIVE/examples-F.jsonl; [ -s "$F" ] || stop 5 "예시 F 파일 없음(kg-init 끝났는지)"
  KAT=$(sed -n 's/^AT=//p' "$LIVE/first-state.env" 2>/dev/null | tail -1)
  [ "$KAT" = "$AT" ] || stop 5 "kg-init AT(${KAT:-없음}) ≠ 앱 AT($AT) — .env AT를 정해 둘이 같게"
  python -m c1a15.demo_history precheck-remove || stop 6 "지난 예시 빼기"
  python -m c1a15.demo_history precheck-add < "$F" || stop 6 "예시 넣기"
  echo "APP_INIT examples ok AT=$AT ROWS=$(grep -c . "$F")"
  ;;
*) stop 2 "쓰는 법: app-init.sh lines | examples" ;;
esac
