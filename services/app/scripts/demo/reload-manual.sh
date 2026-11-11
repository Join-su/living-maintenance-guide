#!/usr/bin/env bash
# 매뉴얼 다시 적재(D-465 · D-471 · D-480 덧 5): 떠 있는 앱 스택 DB의 knowledge.manual_sections에서 글(text)이 바뀐 절만 새 json 값으로 UPDATE → api 다시 시작.
#   사용: reload-manual.sh <real | copy> <manual_sections.json> <백업 폴더>(스택 칸 = stack-env.sh)
#   순서: 1 백업(pg_dump 전체 · gz) 2 DB 절 ↔ json 대조 3 UPDATE(한 트랜잭션 · 지움 0) 4 api 다시 시작 5 확인
#   되돌리기 = 같은 명령에 1에서 남긴 db-sections-before-<시각>.json을 넣음(같은 칸 · 같은 검사).
# 전체 적재기(init · python -m c1a15.loader)는 쓰지 않음: 계정 · 등급 · 공통 사전 확인 행을 지웠다 다시 넣음(운영 자료는 다시 넣지 않음, PR-35).
# 멈춤: 절 id가 다르거나 글 밖의 칸(제목 · 등급 · 분 · 장 · 증상군 · 상위 절)이 바뀌면 손대지 않고 끝(1) — 그건 매뉴얼 개정이 화면 · 등급을 바꾸는 경우.
# api는 매뉴얼을 lru_cache로 들고 있어 다시 시작해야 새 글이 보임(DB · web 손 0). 비밀번호 출력 0.
set -euo pipefail
[ $# -ge 1 ] || { awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 1; }
. "$(dirname "$0")/stack-env.sh" "$1"
NEW=${2:?manual_sections.json}; OUT=${3:?백업 폴더}
[ -f "$ST/stack.env" ] || { echo "스택 없음($ST) — 멈춤"; exit 1; }
set -a; . "$ST/stack.env"; set +a
cd "$R"
mkdir -p "$OUT"
T=$(/bin/date +%Y%m%d-%H%M%S)
psqlq() { docker compose exec -T db psql -U c1a15 -d c1a15 -v ON_ERROR_STOP=1 "$@"; }
counts() { psqlq -tA -c "select string_agg(format('%s.%s=%s', schemaname, relname, n), ' ' order by schemaname, relname) from (select schemaname, relname, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', schemaname, relname), false, true, '')))[1]::text as n from pg_stat_user_tables where schemaname in ('ops', 'knowledge') and relname <> 'manual_sections') t"; }

# 1 백업
docker compose exec -T db pg_dump -U c1a15 -d c1a15 | gzip > "$OUT/$COMPOSE_PROJECT_NAME-before-manual-$T.sql.gz"
[ "$(gzip -dc "$OUT/$COMPOSE_PROJECT_NAME-before-manual-$T.sql.gz" | grep -c 'knowledge.manual_sections')" -gt 0 ] || { echo "백업이 비었음 — 멈춤"; exit 1; }
echo "1 백업 $OUT/$COMPOSE_PROJECT_NAME-before-manual-$T.sql.gz"

# 2 대조 → UPDATE 글
psqlq -tA -c "select json_agg(json_build_object('id', section_id, 'title', title, 'kind', kind, 'chapter', chapter, 'group', symptom_group, 'grade', initial_grade, 'minutes', minutes, 'parent', parent_section, 'text', text)) from knowledge.manual_sections" > "$OUT/db-sections-before-$T.json"
python3 -I - "$OUT/db-sections-before-$T.json" "$NEW" "$OUT/update-$T.sql" <<'PY'
import json, sys
db = {s["id"]: s for s in json.load(open(sys.argv[1], encoding="utf-8"))}
new = {s["id"]: s for s in json.load(open(sys.argv[2], encoding="utf-8"))}
if set(db) != set(new):
    sys.exit(f"멈춤: 절 id 다름 — DB만 {sorted(set(db) - set(new))} · json만 {sorted(set(new) - set(db))}")
other = [(i, f) for i in new for f in ("title", "kind", "chapter", "group", "grade", "minutes", "parent") if db[i].get(f) != new[i].get(f)]
if other:
    sys.exit(f"멈춤: 글 밖의 칸이 바뀜 {other}")
changed = sorted(i for i in new if (db[i].get("text") or "") != (new[i].get("text") or ""))
tag = "m" + "x" * 8
assert all(f"${tag}$" not in (new[i]["text"] or "") for i in changed)
with open(sys.argv[3], "w", encoding="utf-8") as f:
    for i in changed:
        f.write(f"update knowledge.manual_sections set text = ${tag}${new[i]['text']}${tag}$ where section_id = '{i}';\n")
print(f"2 대조: 절 {len(new)} 같음 · 글 밖 칸 바뀜 0 · 글 바뀐 절 {len(changed)} {changed}")
PY
before=$(counts)
psqlq -q -1 < "$OUT/update-$T.sql"
after=$(counts)
[ "$before" = "$after" ] || { echo "운영 · 지식 표 행 수가 바뀜 — 확인 필요"; echo "전 $before"; echo "뒤 $after"; exit 1; }
echo "3 UPDATE 끝 · 다른 표 행 수 그대로($(echo "$after" | wc -w | tr -d ' ')개 표)"

# 4 api 다시 시작(매뉴얼 lru_cache)
docker compose restart api >/dev/null 2>&1
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "api가 안 뜸"; exit 1; }
echo "4 api 다시 시작 · health 200"

# 5 확인: 다시 대조하면 글 바뀐 절 0
psqlq -tA -c "select json_agg(json_build_object('id', section_id, 'text', text)) from knowledge.manual_sections" > "$OUT/db-sections-after-$T.json"
python3 -I - "$OUT/db-sections-after-$T.json" "$NEW" <<'PY'
import json, sys
db = {s["id"]: s["text"] or "" for s in json.load(open(sys.argv[1], encoding="utf-8"))}
new = {s["id"]: s.get("text") or "" for s in json.load(open(sys.argv[2], encoding="utf-8"))}
left = [i for i in new if db.get(i) != new[i]]
print(f"5 확인: DB ↔ json 글 다른 절 {len(left)}")
sys.exit(1 if left else 0)
PY
