#!/usr/bin/env bash
# 시연 준비 상태 — 한 줄로 '문서 04 1 ~ 8까지 끝난 상태'(프로젝트 · V1 운영 · 시험 묶음 둘 · 첫 평가 · 고정 흐름 확인 · link)로 만듭니다.
#
#   scripts/demo-ready.sh            기준 시각(AT)이 24시간 안인 저장본이 있으면 그것으로 되돌림(1 ~ 2분)
#                                    없거나 지났으면 이 스택의 볼륨을 비우고 지금 시각으로 새로 띄운 뒤 문서 04 1 ~ 8을 자동으로 하고 저장(약 6 ~ 8분 · 실제 AI 평가 두 번 · 몇 센트)
#   scripts/demo-ready.sh new        AT와 상관없이 새로 만듦(위 둘째 길)
#   scripts/demo-ready.sh eff-model      효율 ① 출발 상태(문서 08 1 ~ 3 끝: V2 · 운영 · 다음은 화면 '효율 개선 시작')로 — 저장본이 24시간 안이면 되돌림,
#   scripts/demo-ready.sh eff-structure  효율 ②도 같음      아니면 ready 상태에서 V2 등록 · 묶음 · 평가 · 고정 흐름 · efficiency-start를 자동으로 하고 저장(뒤에 new를 붙이면 늘 새로)
#   scripts/demo-ready.sh accuracy       정확도 '자동 개선을 기다린 뒤' 상태(문서 07 1 ~ 5 끝: 'V3 ✦ 자동 · 승인 대기' · 다음은 07 6번 배포 승인)로 — 저장본이 24시간 안이면 되돌림,
#                                    아니면 ready 상태에서 M-13 알람 → 4.6 정상(헛걸음) → 4.3 이상 → 보고서 확정 → 상급자 승인(앱 API) → 자동 개선 ⑦까지 기다림 → 저장
#                                    (약 1시간 반 · AWS t3.large · 실제 AI 몇 센트 · 터미널이 끊기면 멈추므로 서버에서는 nohup · tmux로; 뒤에 new를 붙이면 늘 새로)
#                                    메인 · 정확도를 처음부터 걸려면 인자 없이(ready) · 시나리오마다 이 한 줄로 시작하면 앞 시나리오가 남긴 것은 사라짐(before-<시각>에 남음)
#   scripts/demo-ready.sh save [이름]     지금 상태를 저장(기본 ready)
#   scripts/demo-ready.sh restore [이름]  저장본으로 되돌림(AT가 지났으면 멈춤 — 새로 만들려면 인자 없이 또는 new)
#   scripts/demo-ready.sh list            저장본 · AT · 만든 시각
#
# 왜 24시간: 앱 · 그래프의 시연 줄은 AT 기준 '며칠 전'으로 만들어지고, AT에서 24시간 안일 때만 첫 상태로 봅니다(저장본도 같음).
# 무엇을 지우나: 'new' 길은 이 저장소 폴더의 compose 프로젝트(.env의 COMPOSE_PROJECT_NAME) 볼륨만 지웁니다(docker compose down -v).
#   지우기 전 지금 상태를 demo-snapshots/before-<시각>/에 먼저 저장합니다. 되돌리기 전에도 같음. 다른 compose 프로젝트 · 볼륨은 손대지 않습니다.
# 저장 자리: demo-snapshots/<이름>/(git이 무시) · 볼륨마다 .tgz + .env.agentops + manifest.txt
# 같은 컴퓨터 · 같은 .env(비밀번호)에서만 되돌립니다 — DB가 처음 켤 때의 비밀번호로 만들어지므로 다르면 멈춤(값 출력 0 · 지문만 대조).
# 바꿀 수 있는 값(환경): DEMO_PROJECT(기본 'C1-a15 정비 가이드') · DEMO_DECIDER(고정 흐름 결정자 별명 · 기본 owner-01)
#   DEMO_ACC_MAX_MIN(accuracy가 자동 개선을 기다리는 최대 분 · 기본 180) · DEMO_ACC_MIN_H(accuracy 바탕 ready가 이 시간 넘게 남았을 때만 되돌려 씀 · 기본 12 · 아니면 새로)
# 시험용: DEMO_READY_NO_UP=1(save · restore 뒤 켜지 않음) · DEMO_READY_NO_BEFORE=1(되돌리기 전 저장 안 함)
#   DEMO_READY_ACC_WALK_ONLY=1(accuracy 새로 만들기에서 agentops를 멈춘 채 앱 길(알람 ~ 승인)만 하고 끝 — 자동 개선 · 저장 0)
# 끝 줄(성공): DEMO_READY ok … · 끝 코드: 0 성공 · 2 입력 틀림 · 3 저장본 없음/깨짐/AT 지남 · 4 비밀번호 다름 · 5 서비스가 안 뜸 · 6 자동 단계 실패(문서 04 · 07 · 08)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
set -a; [ -f .env ] && . ./.env; set +a
P=${COMPOSE_PROJECT_NAME:-lmg}
DEMO_PROJECT=${DEMO_PROJECT:-C1-a15 정비 가이드}
DEMO_DECIDER=${DEMO_DECIDER:-owner-01}
VALID_H=24
dc() { docker compose ${LMG_COMPOSE_ARGS:-} "$@"; }
say() { echo "demo-ready: $*"; }
stop() { local c=$1; shift; echo "demo-ready: 멈춤 — $*" >&2; exit "$c"; }
T0=$(date +%s)
took() { echo "$(( $(date +%s) - T0 ))초"; }
SNAPS="$ROOT/demo-snapshots"
HELPER=lmg/agentops:local                                    # 스택에 이미 있는 이미지(tar · python) · 망 없이 돎
VOLS=$(dc config --volumes)
[ -n "$VOLS" ] || stop 2 "compose 볼륨 목록이 비었음(저장소 폴더 · .env 확인)"
sha() { if command -v sha256sum >/dev/null; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }
pwprint() { printf '%s\n' "${TRACE_DB_PASSWORD:-}" "${POSTGRES_PASSWORD:-}" "${C1A15_TECH_PASSWORD:-}" "${C1A15_LEAD_PASSWORD:-}" "${C1A15_INTERNAL_TOKEN:-}" "${NEO4J_AUTH:-}" | sha; }
volname() { docker volume ls -q --filter "label=com.docker.compose.project=$P" --filter "label=com.docker.compose.volume=$1" | head -1; }
helper() { docker run --rm --network none --entrypoint "$1" "${@:2}"; }
images() { dc config --images | sort | xargs docker image inspect --format '{{.Id}}' 2>/dev/null | cut -c8-19 | tr '\n' ','; }
mf() { sed -n "s/^$2=//p" "$1/manifest.txt" 2>/dev/null | head -1; }

save_to() {   # $1 = 폴더(없어야 함) · 스택은 멈춰 있어야 함
  local d=$1 v n at
  mkdir -p "$d.part"
  for v in $VOLS; do
    n=$(volname "$v"); [ -n "$n" ] || stop 3 "볼륨 $v 없음(스택을 한 번 켠 뒤 저장)"
    helper tar -v "$n":/v:ro -v "$d.part":/s "$HELPER" -czf "/s/$v.tgz" -C /v .
  done
  [ -f .env.agentops ] && cp -p .env.agentops "$d.part/env.agentops"
  at=$(helper sh -v "$(volname seed-lines)":/v:ro "$HELPER" -c "sed -n 's/^AT=//p' /v/at.env 2>/dev/null | tail -1" || true)
  {
    echo "made=$(date '+%Y-%m-%dT%H:%M:%S%z')"
    echo "project=$P"
    echo "at=${at:-}"
    echo "git=$(git rev-parse --short HEAD 2>/dev/null || echo 없음)"
    echo "pw=$(pwprint)"
    echo "linked=$([ -f .env.agentops ] && echo yes || echo no)"
    echo "images=$(images)"
    (cd "$d.part" && for f in *.tgz; do echo "file $f $(sha < "$f")"; done)
  } > "$d.part/manifest.txt"
  mv "$d.part" "$d"
}

hours_left() {   # AT → 유효 시간이 몇 시간 남았나(정수 · 음수면 지남) · 모르면 빈 값
  [ -n "${1:-}" ] || return 0
  helper python "$HELPER" -c "import datetime as d,sys; a=d.datetime.fromisoformat(sys.argv[1]); print(int(((a+d.timedelta(hours=$VALID_H))-d.datetime.now(a.tzinfo)).total_seconds()//3600))" "$1" 2>/dev/null || true
}

up_all() {   # docker compose up -d · 처음 켤 때 건강 검사 기다림이 넘치면 한 번 더(되살린 DB는 첫 기동이 느릴 수 있음)
  local log; log=$(mktemp)
  dc up -d >"$log" 2>&1 || { sleep 10; dc up -d >"$log" 2>&1; } || { tail -5 "$log" >&2; rm -f "$log"; stop 5 "docker compose up -d 실패 — docker compose ps · docker compose logs <이름>"; }
  rm -f "$log"
}

wait_up() {   # AgentOps · Pack 서버 · 앱이 대답할 때까지(최대 300초)
  local i ok
  for i in $(seq 1 100); do
    ok=0
    curl -fs -m 3 -o /dev/null "http://127.0.0.1:${AGENTOPS_PORT:-18868}/" && ok=$((ok+1))
    curl -fs -m 3 -o /dev/null "http://127.0.0.1:${PACK_SERVER_PORT:-8796}/health" && ok=$((ok+1))
    curl -fs -m 3 -o /dev/null "http://127.0.0.1:${TECH_PORT:-8081}/" && ok=$((ok+1))
    [ $ok = 3 ] && return 0
    sleep 3
  done
  return 1
}

state_line() {   # 프로젝트 수 · 첫 프로젝트의 버전 상태 한 줄
  dc exec -T agentops python - "$DEMO_PROJECT" <<'PY' 2>/dev/null || echo '상태 못 읽음'
import json, sys, urllib.request
s = json.loads(urllib.request.urlopen("http://127.0.0.1:18868/api/state", timeout=30).read())
ps = s.get("projects", [])
p = next((x for x in ps if x.get("name") == sys.argv[1]), None)
if not p:
    print(f"프로젝트 {len(ps)}개 · '{sys.argv[1]}' 없음"); sys.exit(0)
rels = ", ".join(f"{r.get('name')} {(r.get('version') or {}).get('badge') or (r.get('version') or {}).get('state', '')} · 고정 흐름 {(r.get('version') or {}).get('fixed_flow', '?')}" for r in p.get("releases", []))
runs = sorted(p.get("runs", []), key=lambda r: r.get("at") or "")
last = (runs[-1].get("input") or {}) if runs else {}
print(f"프로젝트 {len(ps)}개 · {rels} · 시험 묶음 {len(p.get('suites', []))} · 평가 Run {len(runs)} · 마지막 {last.get('name') or last.get('id') or '없음'}")
PY
}

restore_from() {   # $1 = 저장본 폴더
  local S=$1 v n B
  [ "$(mf "$S" pw)" = "$(pwprint)" ] || stop 4 ".env 비밀번호가 저장할 때와 다름 — 저장할 때의 .env로 되돌리거나 새로 만듦(scripts/demo-ready.sh new)"
  [ "$(mf "$S" project)" = "$P" ] || say "주의 — 저장한 스택 이름($(mf "$S" project))과 지금($P)이 다름: 지금 스택에 되돌립니다"
  for v in $VOLS; do
    [ -f "$S/$v.tgz" ] || stop 3 "깨짐: $(basename "$S")/$v.tgz 없음"
    [ "$(grep "^file $v.tgz " "$S/manifest.txt" | cut -d' ' -f3)" = "$(sha < "$S/$v.tgz")" ] || stop 3 "깨짐: $(basename "$S")/$v.tgz 지문 다름"
  done
  [ "$(mf "$S" images)" = "$(images)" ] || say "주의 — 이미지가 저장할 때와 다름(코드를 새로 받았거나 다시 빌드함) · 화면이 다르면 scripts/demo-ready.sh new"
  say "스택 멈춤 …"; dc stop >/dev/null 2>&1
  if [ "${DEMO_READY_NO_BEFORE:-0}" != 1 ] && [ -n "$(volname agentops-data)" ]; then
    B="$SNAPS/before-$(date +%Y%m%d-%H%M%S)"; say "지금 상태를 $(basename "$B")로 먼저 저장 …"; save_to "$B"
  fi
  say "되돌림 …"
  for v in $VOLS; do
    n=$(volname "$v")
    [ -n "$n" ] || { docker volume create --label "com.docker.compose.project=$P" --label "com.docker.compose.volume=$v" "${P}_$v" >/dev/null; n=${P}_$v; }
    helper sh -v "$n":/v -v "$S":/s:ro "$HELPER" -c "find /v -mindepth 1 -delete && tar -xzf /s/$v.tgz -C /v"
  done
  if [ -f "$S/env.agentops" ]; then cp -p "$S/env.agentops" .env.agentops; elif [ -f .env.agentops ]; then mv .env.agentops ".env.agentops.before-$(date +%Y%m%d-%H%M%S)"; fi
  [ "${DEMO_READY_NO_UP:-0}" = 1 ] && { echo "DEMO_READY restored(켜지 않음) 이름=$(basename "$S") · 걸린 $(took)"; exit 0; }
  say "켬 …"; up_all
  wait_up || stop 5 "서비스가 300초 안에 대답하지 않음 — docker compose ps"
}

make_new() {   # 볼륨 비움(이 프로젝트만) → 지금 AT로 up → 문서 04 2 ~ 6(API) → 7(link) → 8 확인 → ready로 저장
  local B
  mkdir -p "$SNAPS"
  if [ -n "$(volname agentops-data)" ]; then
    say "스택 멈춤 …"; dc stop >/dev/null 2>&1
    B="$SNAPS/before-$(date +%Y%m%d-%H%M%S)"; say "지금 상태를 $(basename "$B")로 먼저 저장 …"; save_to "$B"
  fi
  say "이 스택($P)의 볼륨만 비움(docker compose down -v) …"
  dc down -v >/dev/null 2>&1 || stop 5 "docker compose down -v 실패"
  [ -f .env.agentops ] && mv .env.agentops ".env.agentops.before-$(date +%Y%m%d-%H%M%S)"
  AT=$(TZ=Asia/Seoul date '+%Y-%m-%dT%H:%M:00+09:00'); export AT
  say "새로 켬 · AT $AT(앱 · 그래프 첫 상태) …"
  up_all
  wait_up || stop 5 "서비스가 300초 안에 대답하지 않음 — docker compose ps"
  say "문서 04 2 ~ 6(프로젝트 · V1 · 시험 묶음 둘 · 첫 평가 둘 · 고정 흐름) — 실제 AI 평가 두 번(약 3 ~ 4분) …"
  dc exec -T agentops python - "$DEMO_PROJECT" "$DEMO_DECIDER" <<'PY' || stop 6 "문서 04 자동 단계 실패(위 줄) — 화면에서 이어 하거나 scripts/demo-ready.sh new 다시"
import json, sys, time, urllib.request, urllib.error
NAME, BY = sys.argv[1], sys.argv[2]
B = "http://127.0.0.1:18868"
def api(m, path, body=None):
    req = urllib.request.Request(B + path, method=m, data=None if body is None else json.dumps(body).encode(), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"  {m} {path.split('?')[0]} → HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
st = api("GET", "/api/state")
if any(p.get("name") == NAME for p in st.get("projects", [])):
    sys.exit(f"  이미 '{NAME}' 프로젝트가 있음 — 새 볼륨이 아님")
prov = next((x for x in st.get("providers", []) if x.get("id") == "openai"), {})
model = prov.get("default_model") or "gpt-4.1-mini"
p = api("POST", "/api/projects", {"name": NAME, "desc": "", "scoring": {"skip": False, "rule": True, "llm": True, "llm_provider": "openai", "llm_model": model},
                                  "rule": {"pass": 0.8, "below": "hold"}, "data": None})
pid = p["id"]; print(f"  2 프로젝트 {NAME} = {pid}")
src = {"how": "local", "path": "/packs/first", "config": ""}
rd = api("POST", f"/api/projects/{pid}/releases/read", src)
if rd and rd.get("error"):
    sys.exit(f"  3 읽기 실패: {rd.get('error')}")
pk = (rd or {}).get("pack") or {}
cfg = next((c for c in pk.get("configs", []) if c.rsplit("/", 1)[-1] == "container.yaml"), None)   # 화면과 같은 값: <읽은 폴더>/<고른 실행 설정>
if not cfg:
    sys.exit(f"  3 실행 설정 목록에 container.yaml 없음: {pk.get('configs')}")
rel = api("POST", f"/api/projects/{pid}/releases", {"name": "V1", "how": "local", "path": "/packs/first", "config": f"{pk['work_folder']}/{cfg}"})
rid = rel["id"]; print(f"  3 V1 등록 = {rid}")
suites = {}
for s in ("c1a15-report-confirm-v4", "c1a15-order"):
    r = api("POST", f"/api/projects/{pid}/eval-suites", {"release": rid, "suite": s})
    cs = r.get('cases'); suites[s] = r; print(f"  4 시험 묶음 {r.get('name', s)} · {len(cs) if isinstance(cs, list) else cs} case")
def run_suite(s):
    run = api("POST", f"/api/projects/{pid}/runs", {"release": rid, "input": {"kind": "suite", "id": suites[s]["id"]}})
    uid = run.get("uid") or run["id"]; t = time.time()
    while run.get("status") == "실행 중":
        if time.time() - t > 900: sys.exit(f"  5 평가 {s} 15분 넘음")
        time.sleep(5); run = api("GET", f"/api/runs/{uid}")
    c = run.get("summary") or {}
    print(f"  5 평가 {s} → {run.get('status')} · {int(time.time() - t)}초 · " + " · ".join(f"{k} {v}" for k, v in c.items() if not isinstance(v, (dict, list))))
    return run.get("status")
for s in ("c1a15-report-confirm-v4", "c1a15-order"):   # 마지막 평가 = c1a15_order(정확도 자동 개선은 마지막 '완료' 평가 묶음으로 돎)
    if run_suite(s) != "완료" and run_suite(s) != "완료":
        sys.exit(f"  5 평가 {s}가 두 번 다 '완료'가 아님 — docker compose logs agentops --tail 50")
ff = api("GET", f"/api/projects/{pid}/releases/{rid}/fixed-flow")
if not ff.get("confirmed_by"):
    ff = api("POST", f"/api/projects/{pid}/releases/{rid}/fixed-flow/confirm", {"by": BY})
print(f"  6 고정 흐름 확인 · 결정자 {ff.get('confirmed_by') or BY}")
PY
  say "문서 04 7(운영 지정 + 운영 서버와 잇기) …"
  bash scripts/link-agentops.sh "$DEMO_PROJECT" || stop 6 "scripts/link-agentops.sh 실패"
  wait_up || stop 5 "link 뒤 서비스가 대답하지 않음"
  say "문서 04 8 확인: $(state_line)"
  if [ -e "$SNAPS/ready" ]; then mv "$SNAPS/ready" "$SNAPS/ready.old-$(date +%Y%m%d-%H%M%S)"; fi
  say "준비 상태를 ready로 저장 …"; dc stop >/dev/null 2>&1; save_to "$SNAPS/ready"
  up_all
  wait_up || stop 5 "서비스가 300초 안에 대답하지 않음"
}

make_eff() {   # $1 = eff-model | eff-structure · ready 상태(되돌리거나 새로) → 문서 08 1 ~ 3(V2 등록 · 묶음 둘 · 평가 · 고정 흐름 · efficiency-start) → $1로 저장
  local K=$1 S="$SNAPS/ready" h=""
  [ -f "$S/manifest.txt" ] && h=$(hours_left "$(mf "$S" at)")
  if [ -n "$h" ] && [ "$h" -ge 0 ]; then say "바탕: 저장본 ready(유효 ${h}시간 남음)로 되돌림 …"; restore_from "$S"; else say "바탕: ready가 없거나 지남 → 새로 만듦 …"; make_new; fi
  say "문서 08 1 ~ 2($K: V2 등록 · 시험 묶음 둘 · 첫 평가 · 고정 흐름) — 실제 AI 평가 한 번 …"
  dc exec -T agentops python - "$DEMO_PROJECT" "$DEMO_DECIDER" "$K" <<'PY' || stop 6 "문서 08 자동 단계 실패(위 줄) — 화면에서 이어 하거나 같은 명령 다시"
import json, sys, time, urllib.request, urllib.error
NAME, BY, K = sys.argv[1], sys.argv[2], sys.argv[3]
SUITES = {"eff-model": ("c1a15-split-work", "c1a15-split-confirm"), "eff-structure": ("c1a15-report-work-v4", "c1a15-report-confirm-v4")}[K]
B = "http://127.0.0.1:18868"
def api(m, path, body=None, ok409=False):
    req = urllib.request.Request(B + path, method=m, data=None if body is None else json.dumps(body).encode(), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        if ok409 and e.code == 409:
            return None
        sys.exit(f"  {m} {path.split('?')[0]} → HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
def project():
    return next((x for x in api("GET", "/api/state").get("projects", []) if x.get("name") == NAME), None)
p = project()
if not p:
    sys.exit(f"  '{NAME}' 프로젝트 없음 — 먼저 scripts/demo-ready.sh")
pid = p["id"]
if any((r.get("pack") or {}).get("path", r.get("path", "")).rstrip("/").endswith("/" + K) or r.get("path", "").rstrip("/").endswith("/" + K) for r in p.get("releases", [])):
    sys.exit(f"  /packs/{K}가 이미 등록돼 있음 — ready 상태가 아님")
nums = [int(r["name"][1:]) for r in p.get("releases", []) if r.get("name", "")[:1] == "V" and r["name"][1:].isdigit()]
vname = f"V{max(nums, default=0) + 1}"
rd = api("POST", f"/api/projects/{pid}/releases/read", {"how": "local", "path": f"/packs/{K}", "config": ""})
pk = (rd or {}).get("pack") or {}
cfg = next((c for c in pk.get("configs", []) if c.rsplit("/", 1)[-1] == "container.yaml"), None)
if not cfg:
    sys.exit(f"  1 /packs/{K} 실행 설정 목록에 container.yaml 없음: {pk.get('configs')}")
rel = api("POST", f"/api/projects/{pid}/releases", {"name": vname, "how": "local", "path": f"/packs/{K}", "config": f"{pk['work_folder']}/{cfg}"})
rid = rel["id"]; print(f"  1 {vname} 등록(/packs/{K}) = {rid}")
ids = {}
for s in SUITES:
    r = api("POST", f"/api/projects/{pid}/eval-suites", {"release": rid, "suite": s}, ok409=True)
    if r is None:   # 같은 내용이 이미 등록됨(문서 04에서 올린 report-confirm-v4 등) → 그것을 씀
        r = next((x for x in project().get("suites", []) if x.get("name") == s.replace("-", "_")), None)
        if not r:
            sys.exit(f"  2 묶음 {s}: 409인데 같은 이름을 못 찾음")
        print(f"  2 시험 묶음 {r['name']} · 이미 있음 — 그대로 씀")
    else:
        cs = r.get("cases"); print(f"  2 시험 묶음 {r.get('name', s)} · {len(cs) if isinstance(cs, list) else cs} case")
    ids[s] = r["id"]
def run_suite(s):
    run = api("POST", f"/api/projects/{pid}/runs", {"release": rid, "input": {"kind": "suite", "id": ids[s]}})
    uid = run.get("uid") or run["id"]; t = time.time()
    while run.get("status") == "실행 중":
        if time.time() - t > 900: sys.exit(f"  2 평가 {s} 15분 넘음")
        time.sleep(5); run = api("GET", f"/api/runs/{uid}")
    c = run.get("summary") or {}
    print(f"  2 평가 {vname} {s} → {run.get('status')} · {int(time.time() - t)}초 · " + " · ".join(f"{k} {v}" for k, v in c.items() if not isinstance(v, (dict, list))))
    return run.get("status")
if run_suite(SUITES[0]) != "완료" and run_suite(SUITES[0]) != "완료":
    sys.exit(f"  2 평가 {SUITES[0]}가 두 번 다 '완료'가 아님 — docker compose logs agentops --tail 50")
ff = api("GET", f"/api/projects/{pid}/releases/{rid}/fixed-flow")
if not ff.get("confirmed_by"):
    ff = api("POST", f"/api/projects/{pid}/releases/{rid}/fixed-flow/confirm", {"by": BY})
print(f"  2 고정 흐름 확인 · 결정자 {ff.get('confirmed_by') or BY}")
PY
  say "문서 08 3(출발 버전을 운영으로 · 알람 한 건) …"
  bash scripts/efficiency-start.sh "$K" || stop 6 "scripts/efficiency-start.sh $K 실패(위 줄)"
  wait_up || stop 5 "efficiency-start 뒤 서비스가 대답하지 않음"
  say "문서 08 3 확인: $(state_line)"
  if [ -e "$SNAPS/$K" ]; then mv "$SNAPS/$K" "$SNAPS/$K.old-$(date +%Y%m%d-%H%M%S)"; fi
  say "효율 출발 상태를 ${K}로 저장 …"; dc stop >/dev/null 2>&1; save_to "$SNAPS/$K"
  up_all
  wait_up || stop 5 "서비스가 300초 안에 대답하지 않음"
}

# 문서 07 2 ~ 4를 앱 API로(정비자 tech.kim · 상급자 lead.park) — 비밀번호는 표준 입력 두 줄로만 받음(명령 줄 · 환경 · 출력 0)
IFS= read -r -d '' ACC_WALK_PY <<'PY' || true
import json, sys, urllib.request, urllib.error
TP, LP = sys.stdin.readline().rstrip("\n"), sys.stdin.readline().rstrip("\n")
EQ = sys.argv[1]
B = "http://127.0.0.1:8000"   # api 컨테이너 안 — 앱 화면(nginx)이 붙이는 X-App을 직접 붙임
MEMO = "흡기 필터 표면이 먼지로 막혀 있음"
class App:
    def __init__(self, app, user, pw):
        self.app, self.cookie = app, ""
        self.call("POST", "/api/login", {"user_id": user, "password": pw}, login=True)
    def call(self, m, path, body=None, login=False):
        h = {"content-type": "application/json", "x-app": self.app}
        if self.cookie:
            h["cookie"] = self.cookie
        req = urllib.request.Request(B + path, method=m, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                if login:
                    self.cookie = "; ".join(c.split(";", 1)[0] for c in r.headers.get_all("set-cookie") or [])
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            sys.exit(f"  {self.app} {m} {path.split('?')[0]} → HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
tech = App("tech", "tech.kim", TP)
mine = [a for a in tech.call("GET", "/api/tech/home").get("alarms", []) if a.get("equipment_id") == EQ and a.get("status") == "new" and not a.get("session_id")]
if not mine:
    sys.exit(f"  2 정비자 홈에 {EQ} 새 알람이 없음(scripts/app-alarm.sh {EQ}가 등록을 못 함?)")
aid = max(mine, key=lambda a: a["alarm_id"])["alarm_id"]
s = tech.call("POST", f"/api/tech/alarms/{aid}/session")
def ev(**body):
    global s
    s = tech.call("POST", f"/api/tech/sessions/{s['session_id']}/events", {"seq": s["last_seq"] + 1, **body})
def checklist_first():
    st = s.get("step") or {}
    if st.get("kind") in ("instruct_after_checklist", "action_after_checklist"):
        ev(type="b_checklist_done", section=st.get("section"), checked=(s.get("instruction") or {}).get("checklist") or (s.get("action") or {}).get("checklist") or [])
ev(type="alarm_ack"); ev(type="lock"); ev(type="precheck_done", values="하우징 52 °C")
path = []
for _ in range(6):
    ins = s.get("instruction") or {}
    sec = ins.get("section")
    if not sec:
        sys.exit(f"  3 점검 안내가 없음 — 단계 {(s.get('step') or {}).get('kind')} · 지나온 점검 {path}")
    checklist_first()
    if sec == "4.3":
        ev(type="result", section=sec, value="이상", memo=MEMO); path.append(f"{ins.get('title')}({sec}, 이상)"); break
    ev(type="result", section=sec, value="정상"); path.append(f"{ins.get('title')}({sec}, 정상)")
else:
    sys.exit(f"  3 4.3이 여섯 점검 안에 안 나옴: {path}")
print(f"  2 알람 {EQ} #{aid} · 첫 점검 {path[0]}", flush=True)
if len(path) < 2:
    sys.exit("  3 첫 점검이 바로 4.3 — 헛걸음이 생기지 않음(이미 개선된 버전이 운영 중?) · ready 상태에서 다시")
checklist_first()
ev(type="action_done", section=(s.get("action") or {}).get("section"))
ev(type="restart", checked=(s.get("restart") or {}).get("checklist") or [])
ev(type="restart_result", outcome="within")
ev(type="report_confirm")
print(f"  3 점검 경로 {' → '.join(path)} · 보고서 확정", flush=True)
lead = App("lead", "lead.park", LP)
row = next((r for r in lead.call("GET", "/api/lead/overview").get("rows", []) if r.get("session_id") == s["session_id"]), None)
if not row:
    sys.exit("  4 상급자 승인 대기에 이 보고서가 없음")
lead.call("POST", f"/api/lead/reports/{row['report_id']}/decision", {"decision": "approve"})
print(f"  4 상급자 승인 · 보고서 {row['report_id']} · {row.get('line') or row.get('cause') or ''}", flush=True)
PY

# 문서 07 4 ~ 5: 승인 뒤 그래프 반영 새 버전 · '사람 체크로 시작' 자동 개선이 '⑦ 승인 · 사람 차례'가 되고 ✦ 버전이 '승인 대기'일 때까지(1분마다 한 번)
IFS= read -r -d '' ACC_WAIT_PY <<'PY' || true
import json, sys, time, urllib.request
NAME, MAXMIN = sys.argv[1], int(sys.argv[2])
B = "http://127.0.0.1:18868"
def get(path):
    for i in range(5):
        try:
            return json.loads(urllib.request.urlopen(B + path, timeout=120).read())
        except Exception as e:   # 서버가 잠깐 바쁨(후보 시험 중) — 다음 분에 다시
            err = e; time.sleep(30)
    sys.exit(f"  5 AgentOps {path.split('?')[0]} 대답 없음: {type(err).__name__}")
def project():
    return next((x for x in get("/api/state").get("projects", []) if x.get("name") == NAME), None)
p = project()
if not p:
    sys.exit(f"  5 '{NAME}' 프로젝트 없음")
t0, last = time.time(), None
while True:
    mins = int((time.time() - t0) / 60)
    if mins >= MAXMIN:
        sys.exit(f"  5 {MAXMIN}분이 지나도 '⑦ 승인 · 사람 차례'가 아님 — 마지막: {last} · 화면 ✦ 자동 개선 · docker compose logs agentops --tail 50")
    ws = get(f"/api/projects/{p['id']}/auto").get("wheels") or []
    if not ws:
        if mins >= 15:
            sys.exit("  5 승인 뒤 15분이 지나도 자동 개선이 시작되지 않음 — 화면 V1 운영 Run '사람: 헛걸음' 줄 · docker compose logs agentops --tail 50")
    else:
        w = ws[0]
        line = f"{w['name']} · {w.get('status')}"
        if line != last:
            print(f"  5 {mins}분 {line}", flush=True); last = line
        if w.get("outcome") or w.get("cell_state") == "stopped":
            sys.exit(f"  5 자동 개선이 ⑦ 전에 끝남 · 멈춤: {line}")
        if w.get("cell") == 7 and w.get("cell_state") == "human":
            p = project()
            r = next((r for r in p.get("releases", []) if (r.get("version") or {}).get("badge") == "승인 대기"), None)
            if r:
                print(f"  5 {mins}분 {r['name']} ✦ 자동 · 승인 대기 · 바탕 {(w.get('base') or {}).get('name')}", flush=True)
                break
    time.sleep(60)
PY

make_acc() {   # ready 상태(되돌리거나 새로) → 문서 07 2 ~ 4(앱 API) → 5(자동 개선 ⑦까지 기다림) → accuracy로 저장
  local S="$SNAPS/ready" h="" min=${DEMO_ACC_MIN_H:-12}
  [ -f "$S/manifest.txt" ] && h=$(hours_left "$(mf "$S" at)")
  if [ -n "$h" ] && [ "$h" -ge "$min" ]; then say "바탕: 저장본 ready(유효 ${h}시간 남음)로 되돌림 …"; restore_from "$S"; else say "바탕: ready가 없거나 남은 시간이 ${min}시간 아래(${h:-없음}) → 새로 만듦 …"; make_new; fi
  if [ "${DEMO_READY_ACC_WALK_ONLY:-0}" = 1 ]; then say "시험: agentops 멈춤(자동 개선 0)"; dc stop agentops >/dev/null 2>&1; fi
  say "문서 07 2 ~ 4(M-13 알람 → 4.6 정상 · 헛걸음 → 4.3 이상 → 보고서 확정 → 상급자 승인) …"
  bash scripts/app-alarm.sh M-13 >/dev/null || stop 6 "scripts/app-alarm.sh M-13 실패"
  printf '%s\n%s\n' "${C1A15_TECH_PASSWORD:-}" "${C1A15_LEAD_PASSWORD:-}" | dc exec -T api python -c "$ACC_WALK_PY" M-13 \
    || stop 6 "문서 07 2 ~ 4 자동 단계 실패(위 줄) — scripts/demo-ready.sh accuracy new로 다시"
  if [ "${DEMO_READY_ACC_WALK_ONLY:-0}" = 1 ]; then echo "DEMO_READY acc-walk ok(agentops 멈춤 · 저장 0) · 걸린 $(took) — 되돌리려면 scripts/demo-ready.sh"; exit 0; fi
  say "문서 07 5(자동 개선 — 누름 0 · 약 40분 · AWS t3.large 약 1시간 반 · 최대 ${DEMO_ACC_MAX_MIN:-180}분 기다림) …"
  dc exec -T agentops python -c "$ACC_WAIT_PY" "$DEMO_PROJECT" "${DEMO_ACC_MAX_MIN:-180}" || stop 6 "문서 07 5 자동 개선 기다림 실패(위 줄)"
  say "문서 07 5 확인: $(state_line)"
  if [ -e "$SNAPS/accuracy" ]; then mv "$SNAPS/accuracy" "$SNAPS/accuracy.old-$(date +%Y%m%d-%H%M%S)"; fi
  say "정확도 '승인 대기' 상태를 accuracy로 저장 …"; dc stop >/dev/null 2>&1; save_to "$SNAPS/accuracy"
  up_all
  wait_up || stop 5 "서비스가 300초 안에 대답하지 않음"
}

CMD=${1:-ready}
case "$CMD" in
  list)
    [ -d "$SNAPS" ] || { say "저장본 없음($SNAPS)"; exit 0; }
    for d in "$SNAPS"/*/; do
      [ -f "$d/manifest.txt" ] || continue
      h=$(hours_left "$(mf "$d" at)")
      printf '%-28s AT %-26s %s · 만든 %s · link %s\n' "$(basename "$d")" "$(mf "$d" at)" "${h:+$( [ "$h" -ge 0 ] && echo "유효 ${h}시간 남음" || echo "지남")}" "$(mf "$d" made)" "$(mf "$d" linked)"
    done
    exit 0 ;;
  save)
    NAME=${2:-ready}
    [[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || stop 2 "이름은 영문 · 숫자 · . _ - 만: $NAME"
    [ -e "$SNAPS/$NAME" ] && stop 2 "이미 있음: demo-snapshots/$NAME — 다른 이름을 주거나 그 폴더를 옮긴 뒤 다시"
    [ -f .env.agentops ] || say "주의 — .env.agentops 없음: 문서 04 7번(link) 전 상태를 저장합니다"
    mkdir -p "$SNAPS"
    say "스택 멈춤(DB를 닫은 채 저장) …"; dc stop >/dev/null 2>&1
    save_to "$SNAPS/$NAME"
    [ "${DEMO_READY_NO_UP:-0}" = 1 ] && { echo "DEMO_READY saved(켜지 않음) 이름=$NAME · 크기 $(du -sh "$SNAPS/$NAME" | cut -f1) · 걸린 $(took)"; exit 0; }
    say "다시 켬 …"; up_all
    wait_up || stop 5 "서비스가 300초 안에 대답하지 않음 — docker compose ps"
    echo "DEMO_READY saved 이름=$NAME AT=$(mf "$SNAPS/$NAME" at) · link $(mf "$SNAPS/$NAME" linked) · $(state_line) · 크기 $(du -sh "$SNAPS/$NAME" | cut -f1) · 걸린 $(took)"
    ;;
  restore)
    NAME=${2:-ready}; S="$SNAPS/$NAME"
    [ -f "$S/manifest.txt" ] || stop 3 "저장본 없음: demo-snapshots/$NAME — 인자 없이 scripts/demo-ready.sh(새로 만들고 저장)"
    h=$(hours_left "$(mf "$S" at)")
    [ -n "$h" ] && [ "$h" -lt 0 ] && [ "${DEMO_READY_FORCE:-0}" != 1 ] && stop 3 "저장본 ${NAME}의 AT($(mf "$S" at))가 ${VALID_H}시간 지남 — 인자 없이 scripts/demo-ready.sh(새로 만들고 저장)"
    restore_from "$S"
    echo "DEMO_READY ok 되돌림 이름=$NAME AT=$(mf "$S" at)${h:+ (유효 ${h}시간 남음)} · $(state_line) · 걸린 $(took) · 화면을 새로 고치세요(AgentOps http://127.0.0.1:${AGENTOPS_PORT:-18868})"
    ;;
  ready|new)
    S="$SNAPS/ready"; h=""
    [ -f "$S/manifest.txt" ] && h=$(hours_left "$(mf "$S" at)")
    if [ "$CMD" = ready ] && [ -n "$h" ] && [ "$h" -ge 0 ]; then
      say "저장본 ready · AT $(mf "$S" at) · 유효 ${h}시간 남음 → 되돌림"
      restore_from "$S"
      echo "DEMO_READY ok 되돌림 AT=$(mf "$S" at) (유효 ${h}시간 남음) · $(state_line) · 걸린 $(took) · 화면을 새로 고치세요(AgentOps http://127.0.0.1:${AGENTOPS_PORT:-18868})"
    else
      if [ "$CMD" = new ]; then say "new — 새로 만듦"; elif [ -f "$S/manifest.txt" ]; then say "저장본 ready의 AT($(mf "$S" at))가 ${VALID_H}시간 지남 → 새로 만듦"; else say "저장본 ready 없음 → 새로 만듦"; fi
      make_new
      echo "DEMO_READY ok 새로 만듦 AT=$(mf "$S" at) (유효 ${VALID_H}시간) · $(state_line) · 저장 ready · 걸린 $(took) · 화면을 새로 고치세요(AgentOps http://127.0.0.1:${AGENTOPS_PORT:-18868})"
    fi
    ;;
  eff-model|eff-structure)
    S="$SNAPS/$CMD"; h=""
    [ -f "$S/manifest.txt" ] && h=$(hours_left "$(mf "$S" at)")
    if [ "${2:-}" != new ] && [ -n "$h" ] && [ "$h" -ge 0 ]; then
      say "저장본 $CMD · AT $(mf "$S" at) · 유효 ${h}시간 남음 → 되돌림"
      restore_from "$S"
      echo "DEMO_READY ok 되돌림 $CMD AT=$(mf "$S" at) (유효 ${h}시간 남음) · $(state_line) · 걸린 $(took) · AgentOps ✦ 자동 개선 → 효율 개선 시작(문서 08 4번)"
    else
      if [ "${2:-}" = new ]; then say "$CMD new — 새로 만듦"; elif [ -f "$S/manifest.txt" ]; then say "저장본 ${CMD}의 AT($(mf "$S" at))가 ${VALID_H}시간 지남 → 새로 만듦"; else say "저장본 $CMD 없음 → 새로 만듦"; fi
      make_eff "$CMD"
      echo "DEMO_READY ok 새로 만듦 $CMD AT=$(mf "$S" at) · $(state_line) · 저장 $CMD · 걸린 $(took) · AgentOps ✦ 자동 개선 → 효율 개선 시작(문서 08 4번)"
    fi
    ;;
  accuracy)
    S="$SNAPS/accuracy"; h=""
    [ -f "$S/manifest.txt" ] && h=$(hours_left "$(mf "$S" at)")
    NEXT="다음: scripts/app-alarm.sh M-01 → AgentOps V3 ✦ 자동 탭 → 배포 승인 ▶(문서 07 6번)"
    if [ "${2:-}" != new ] && [ -n "$h" ] && [ "$h" -ge 0 ]; then
      say "저장본 accuracy · AT $(mf "$S" at) · 유효 ${h}시간 남음 → 되돌림"
      restore_from "$S"
      echo "DEMO_READY ok 되돌림 accuracy AT=$(mf "$S" at) (유효 ${h}시간 남음) · $(state_line) · 걸린 $(took) · ${NEXT}"
    else
      if [ "${2:-}" = new ]; then say "accuracy new — 새로 만듦"; elif [ -f "$S/manifest.txt" ]; then say "저장본 accuracy의 AT($(mf "$S" at))가 ${VALID_H}시간 지남 → 새로 만듦"; else say "저장본 accuracy 없음 → 새로 만듦"; fi
      make_acc
      echo "DEMO_READY ok 새로 만듦 accuracy AT=$(mf "$S" at) · $(state_line) · 저장 accuracy · 걸린 $(took) · ${NEXT}"
    fi
    ;;
  *) stop 2 "모르는 명령: $CMD — 인자 없이 · new · eff-model · eff-structure · accuracy · save · restore · list" ;;
esac
