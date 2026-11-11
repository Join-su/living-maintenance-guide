#!/usr/bin/env bash
# Record in AgentOps that the Pack the deploy server runs NOW is the operational version of a folder you registered on the screen.
# The efficiency scenario uses it after the deploy server was switched to an efficiency start Pack (eff-model · eff-structure):
# a version registered from a folder cannot be deploy-approved on the screen (that path is for a Pack-side developer change), so the
# deploy server is switched by a script and this records the same thing a deploy approval would (the same admin function as
# link-agentops.sh's first operational version).
#   scripts/agentops-operational.sh [--check] /packs/eff-model [<tag, default = folder name>]
# Writes one ledger row only when the deploy server's revision · data release · deployment id equal the folder's — otherwise nothing
# (exit 3). Running it again changes nothing (exit 0, 'already'). No value is printed.
# --check writes nothing and does not need the switch yet: it only says whether the row could be written after the switch
# (folder registered on the screen · same deployment id · the bootstrap still open). Use it BEFORE switching the deploy server.
# The bootstrap closes for the Pack once AgentOps has a deploy approval for it (store.add_deployment) — e.g. after guide 07's
# '배포 승인'. Then go back to the first state (guide 09) and start guide 08 from there.
# Exit codes: 0 recorded / would record (or already) · 1 usage · 2 the deploy server does not answer · 3 the server runs another Pack
# (or, with --check, another deployment id) · 4 the folder's version is not registered / not operational after · 5 bootstrap closed
set -euo pipefail
cd "$(dirname "$0")/.."
CHECK=0; [ "${1:-}" = --check ] && { CHECK=1; shift; }
PACK=${1:?Pack 폴더를 주세요 — 예: scripts/agentops-operational.sh [--check] /packs/eff-model}
TAG=${2:-$(basename "$PACK")}
set -a; [ -f .env ] && . ./.env; set +a
DEP=${DEPLOYMENT_ID:-c1a15@local}
dc() { docker compose ${LMG_COMPOSE_ARGS:-} "$@"; }
dc exec -T agentops python - "$PACK" "$TAG" "$DEP" "${USER:-team}" "$CHECK" <<'PY'
import json, sys, time, urllib.request
from pathlib import Path
from llmops import admin
from llmops.config import load_settings

pack, tag, dep, who, check = sys.argv[1:6]
check = check == "1"
p = Path(pack)
say = lambda m: print(f"agentops-operational{' --check' if check else ''} · {m}")
if not (p / "pack.yaml").is_file() or not (p / "execution-config.yaml").is_file():
    say(f"{pack}에 pack.yaml · execution-config.yaml 없음"); sys.exit(1)
try:
    w = json.loads(urllib.request.urlopen("http://pack-server:8796/info", timeout=10).read())
except Exception as e:
    say(f"배포 서버 응답 없음({type(e).__name__})"); sys.exit(2)
pack_id, rev, drs, bind, sha, (mrs, mb) = admin.registration(p, p / "execution-config.yaml")
srv = (w["pack"]["pack_revision"], w["data_release_id"], w["deployment_id"])


def registered():
    s = json.loads(urllib.request.urlopen("http://127.0.0.1:18868/api/state", timeout=30).read())
    return [r for pr in s["projects"] for r in pr["releases"] if (r.get("pack") or {}).get("revision") == rev]


st = admin.open_store(load_settings())   # the same backend as the server (as llmops.admin does)
rows = st.deployments()
already = bool(rows) and (rows[-1]["deployment_id"], rows[-1]["pack_revision"], rows[-1]["data_release_id"]) == (dep, rev, drs)
packs, unknown = st.approved_packs()
closed = None if dep.endswith("@dev-local") else (
    f"Pack을 정할 수 없는 배포 승인(decision {unknown[0]})이 있음" if unknown else
    f"이 Pack({pack_id})에 배포 승인(decision {packs[pack_id]})이 있음" if pack_id in packs else None)

if check:
    if srv[2] != dep:
        say(f"배포 서버 deployment id가 다름 — {srv[2]} / {dep}"); sys.exit(3)
    if not registered():
        say(f"{pack} revision {rev[7:15]}의 등록 버전 없음 — 화면 '＋ release'로 먼저 등록"); sys.exit(4)
    if already:
        say(f"이미 운영 줄 있음 · {tag} {rev[7:15]} — 전환해도 줄 안 씀"); sys.exit(0)
    if closed:
        say(f"bootstrap 닫힘 — {closed} · 전환하지 말고 첫 상태(설치 안내 09)에서 다시"); sys.exit(5)
    say(f"전환 뒤 운영 줄 쓸 수 있음 · {tag} {rev[7:15]} · {dep}"); sys.exit(0)

if (rev, drs, dep) != srv:
    say(f"배포 서버가 다른 Pack을 돌림 — 줄 안 씀 · 폴더 {rev[7:15]} {drs} {dep} / 서버 {srv[0][7:15]} {srv[1]} {srv[2]}"); sys.exit(3)
if already:
    say(f"이미 운영 줄 있음 · {tag} {rev[7:15]}")
else:
    if closed:
        say(f"bootstrap 닫힘 — {closed} · 줄 안 씀 · 첫 상태(설치 안내 09)에서 다시"); sys.exit(5)
    try:
        admin.deploy_bootstrap(st, deployment_id=dep, pack_dir=p, execution_config=p / "execution-config.yaml", decided_by=who,
                               reason=f"효율 시나리오 출발 — 배포 서버를 {tag}로 바꾼 뒤 운영 기록(설치 안내 08)", pack_tag=tag)
    except (ValueError, admin.BootstrapError) as e:
        say(f"줄 안 씀 — {e}"); sys.exit(5)
    say(f"운영 줄 씀 · {tag} {rev[7:15]} · {dep}")
for _ in range(20):
    op = [(r["name"], ((r.get("version") or {}).get("fixed_flow") or {}).get("state") if isinstance((r.get("version") or {}).get("fixed_flow"), dict) else (r.get("version") or {}).get("fixed_flow"))
          for r in registered() if (r.get("version") or {}).get("state") == "운영"]
    if op:
        say(f"운영 {op[0][0]} · 고정 흐름 {op[0][1]}"); sys.exit(0)
    time.sleep(1)
say("줄은 있으나 이 revision의 등록 버전이 운영으로 안 보임 — 화면에서 그 폴더를 먼저 등록했는지 확인"); sys.exit(4)
PY
