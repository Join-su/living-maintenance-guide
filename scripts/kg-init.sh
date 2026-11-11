#!/bin/bash
# kg-init — 한 번 돌고 끝남: 시드 이력 → 지식 그래프(Neo4j 층) → Pack 그래프 파일 → 'live' Pack 폴더(git · kg-apply 가지 · 첫 상태 태그).
# Mac 시연 스택에서 손으로 잇던 차례(first-history.sh → first-build.sh)를 컨테이너 한 번으로 옮긴 것. 값 출력 0(비밀번호는 메모리 · 임시 파일만).
#
# 환경 값(compose가 줌):
#   NEO4J_AUTH   필수 · 'neo4j/<비밀번호>'(Neo4j 컨테이너와 같은 값)
#   KGDB_URI     기본 bolt://neo4j:7687
#   AT           기준 시각 · 비면 $LINES_DIR/at.env(앱이 남김) · 그것도 없으면 지금(KST +09:00) · 꼴 2026-11-11T09:00:00+09:00
#   PACK_LIVE    기본 /srv/pack-repo(볼륨 pack-repo) — 여기에 c1a15-pack/(git · 가지 · 첫 태그) · agent-pack-runtime-ingest/(벡터 DB)가 생김
#                pack-server · deploy-watcher · agentops(LLMOPS_KG_PACK_REPO=/srv/pack-repo/c1a15-pack)가 같은 볼륨을 씀
#   PACKS_DIR    기본 /packs(볼륨 packs) — 첫 태그의 pack/ 내용을 /packs/first에 · 이름표 ui-labels/를 /packs에(AgentOps '＋ release' 로컬 경로 = /packs/first)
#   LINES_DIR    기본 /srv/seed-lines — 앱이 찍은 시연 줄 line-M-*.jsonl(없으면 바탕 이력의 옛 시연 줄 그대로)
#   FIRST_PACK_TAG 기본 first(AgentOps · 문서가 부르는 첫 태그 · kgdiff.TAG 꼴) · PACK_BRANCH 기본 kg-apply
#   WATCH_BASE_TAG 기본 d001-g1 — 같은 커밋에 하나 더(받아 넣기 도구가 다시 태우기 때 바탕으로 찾는 꼴은 g숫자 · d숫자-g숫자뿐 · 비우면 안 만듦)
#   AGENTOPS_EXEC_CONFIG 기본 /srv/agentops-config/execution-config.yaml — 있으면 /packs/first/execution-config.yaml · /packs/first/execution/container.yaml로(등록 화면에서 고를 이름 = container · 없으면 Pack 것 그대로 · 경고)
#   KG_INIT_DRY=1 그래프 DB 앞에서 멈춤(이력만 만들어 봄)
#   FORCE=1      이미 첫 상태가 있어도 새로 만듦(옛 live 폴더는 live.old-<시각>으로 옮김 · 지움 0)
# 남기는 것: $PACK_LIVE/first-state.env · kg-history.jsonl(감시가 이어 씀) · examples-F.jsonl(앱이 넣을 예시 F) · $PACKS_DIR/first · $PACKS_DIR/eff-model · eff-structure
# 끝 줄(성공): KG_INIT ok AT=… FIRST_PACK_TAG=… WATCH_BASE_TAG=… PACK_REVISION=… LAYER=… ROWS=…  · 같은 값을 $PACK_LIVE/first-state.env에 씀
# 끝 코드: 0 성공(또는 이미 있음) · 2 입력 틀림 · 3 그래프 DB 연결 안 됨 · 4 적재 · 그래프 파일 실패 · 5 효율 출발 Pack(/packs/eff-*) 실패 · 6 효율 출발 커밋 · 태그(eff-model · eff-structure) 실패 또는 revision 다름
set -euo pipefail
SRC=${PACK_SRC:-/opt/pack}; ING=${INGEST_SRC:-/opt/agent-pack-runtime-ingest}; SEED=${SEED_DIR:-/opt/seed}; TOOLS=${TOOLS_DIR:-/opt/tools}
LIVE=${PACK_LIVE:-/srv/pack-repo}; PACKS=${PACKS_DIR:-/packs}; LINES_DIR=${LINES_DIR:-/srv/seed-lines}
PY=${PY:-python}
# AT 차례: 환경 값 → 앱이 시드 줄과 함께 남긴 $LINES_DIR/at.env(AT=… 한 줄) → 지금(KST 고정 +09:00 · 이미지 시간대 자료와 무관 · 앱과 같은 꼴)
if [ -z "${AT:-}" ] && [ -f "$LINES_DIR/at.env" ]; then AT=$(sed -n 's/^AT=//p' "$LINES_DIR/at.env" | head -1); fi
if [ -z "${AT:-}" ]; then AT=$("$PY" -c "from datetime import datetime, timedelta, timezone; print(datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S+09:00'))"); fi
FIRST_TAG=${FIRST_PACK_TAG:-${FIRST_TAG:-first}}; WATCH_TAG=${WATCH_BASE_TAG-d001-g1}; EXEC_CFG=${AGENTOPS_EXEC_CONFIG:-/srv/agentops-config/execution-config.yaml}; BRANCH=${PACK_BRANCH:-kg-apply}; export KGDB_URI=${KGDB_URI:-bolt://neo4j:7687}
say() { echo "$(date '+%H:%M:%S') kg-init · $*"; }
stop() { echo "KG_INIT stop($1) · $2"; exit "$1"; }

[ -n "${NEO4J_AUTH:-}" ] || stop 2 "NEO4J_AUTH 없음"
[[ "$AT" =~ ^20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]\+09:00$ ]] || stop 2 "AT 꼴이 다름: $AT"
for f in "$SEED/history-demo-base.jsonl" "$SEED/maintenance_history.jsonl" "$TOOLS/demo_dev2.py" "$SRC/kg-test/graph-v3/loader.py"; do [ -f "$f" ] || stop 2 "없음: $f"; done

P=$LIVE/c1a15-pack
if [ -d "$P/.git" ] && git -C "$P" rev-parse -q --verify "refs/tags/$FIRST_TAG" >/dev/null; then
  if [ "${FORCE:-0}" != 1 ]; then say "이미 첫 상태 $FIRST_TAG — 그대로(FORCE=1이면 새로)"; cat "$LIVE/first-state.env" 2>/dev/null; exit 0; fi
  O=$LIVE.old-$(date '+%m%d-%H%M%S'); mkdir -p "$O"; mv "$P" "$LIVE/agent-pack-runtime-ingest" "$O/" 2>/dev/null || true; say "옛 live → $O"
fi

W=$(mktemp -d); trap 'rm -rf "$W"' EXIT; mkdir -p "$W/empty-inbox"
umask 077; printf 'NEO4J_AUTH=%s\n' "$NEO4J_AUTH" > "$W/neo4j.env"; umask 022; export KGDB_ENV_FILE="$W/neo4j.env"

# 1 live Pack 폴더 = 제품 Pack 트리(c1a15-pack bcaf137) + 벡터 DB 자리(실행 설정 release_roots ../../../agent-pack-runtime-ingest 그대로)
mkdir -p "$LIVE"; cp -a "$SRC/." "$P/"; rm -rf "$P/docker"; cp -a "$ING" "$LIVE/agent-pack-runtime-ingest"
git -C "$P" init -q -b "$BRANCH"; git -C "$P" add -A; git -C "$P" -c user.name=kg-init -c user.email=kg-init@local commit -q -m "제품 Pack(c1a15-pack bcaf137 트리)"
say "live Pack $(git -C "$P" rev-parse --short HEAD) · 가지 $BRANCH"

# 2 그래프 이력 = 바탕(처음 이력 300 + 옛 시연 · 예시 줄) + 앱 시연 줄(AT) + 예시 F(AT) − 첫 승인 때 들어올 시연 줄 H-0301 · H-0302
"$PY" -B -I "$P/kg-test/graph-v3/precheck_examples.py" --base "$SEED/maintenance_history.jsonl" --at "$AT" --out "$W/examples-F.jsonl" --patterns F >/dev/null
L=(); for f in "$LINES_DIR"/line-*.jsonl; do [ -f "$f" ] && L+=(--line "$f"); done
[ ${#L[@]} -gt 0 ] && say "앱 시연 줄 $((${#L[@]} / 2))개" || say "앱 시연 줄 없음 — 바탕의 옛 시연 줄 그대로(앱 시드와 날짜가 어긋날 수 있음)"
"$PY" -B -I "$TOOLS/demo_dev2.py" examples --in "$SEED/history-demo-base.jsonl" --out "$W/history-demo.jsonl" --keep F --app-examples "$W/examples-F.jsonl" ${L[@]+"${L[@]}"} >/dev/null
"$PY" -B -I - "$W/history-demo.jsonl" "$W/history-first.jsonl" <<'PY'
import json, sys
rows = [l for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
keep = [l for l in rows if json.loads(l)["record_id"] not in ("H-0301", "H-0302")]
assert len(rows) - len(keep) == 2, "시연 줄 둘이 아님"
open(sys.argv[2], "w", encoding="utf-8").writelines(keep)
PY
ROWS=$(grep -c . "$W/history-first.jsonl"); say "첫 층 이력 ${ROWS}줄 · AT $AT"
if [ "${KG_INIT_DRY:-0}" = 1 ]; then cp "$W/history-first.jsonl" "$W/examples-F.jsonl" "$LIVE/"; echo "KG_INIT dry AT=$AT ROWS=$ROWS(그래프 DB 앞에서 멈춤 · 이력은 $LIVE/history-first.jsonl)"; exit 0; fi

# 3 그래프 DB 기다림(최대 약 2분)
CONN_DIR="$P/kg-test/graph" "$PY" -B - <<'PY' || stop 3 "그래프 DB 연결 안 됨: $KGDB_URI"
import os, sys, time
sys.path.insert(0, os.environ["CONN_DIR"])
from conn import driver
for i in range(60):
    try:
        with driver() as d:
            d.verify_connectivity(); sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit(1)
PY

# 4 새 층 적재(그래프 DB) → 그래프 파일 → packrt write_file(kg_apply와 같은 길) → 커밋 · 첫 상태 태그
(cd "$P" && "$PY" -B kg-test/graph-v3/loader.py --history "$W/history-first.jsonl" --inbox "$W/empty-inbox" \
  --base pack/templates/graph/current.json --out "$W/current.json" | tail -1) || stop 4 "적재 실패"
REV=$("$PY" -B - "$P" "$W/current.json" <<'PY'
import sys, tempfile
from pathlib import Path
from packrt.ir.patch import apply_patch
repo, new = Path(sys.argv[1]), Path(sys.argv[2]); G = "templates/graph/current.json"
with tempfile.TemporaryDirectory() as d:
    r = apply_patch(repo / "pack", [{"op": "write_file", "path": G, "text": new.read_text(encoding="utf-8")}], out_dir=Path(d) / "out")
    if r.errors: sys.exit(f"packrt patch 거부 {len(r.errors)}건")
    (repo / "pack" / G).write_bytes((Path(d) / "out" / G).read_bytes())
print(r.revision)
PY
) || stop 4 "그래프 파일 쓰기 거부"
LAYER=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['layer'])" "$W/current.json")
git -C "$P" -c user.name=kg-init -c user.email=kg-init@local commit -q -am "첫 상태 그래프(AT $AT · 층 $LAYER · 예시 F · 예외 0)"
git -C "$P" tag "$FIRST_TAG"; [ -n "$WATCH_TAG" ] && [ "$WATCH_TAG" != "$FIRST_TAG" ] && git -C "$P" tag "$WATCH_TAG"
cp "$W/examples-F.jsonl" "$LIVE/examples-F.jsonl"   # 앱 이력 DB에 넣을 예시 F 8줄(같은 AT · 앱 precheck-add 몫)
cp "$W/history-demo.jsonl" "$LIVE/kg-history.jsonl"   # 승인 반영 때 감시가 이어 쓰는 그래프 이력(KG_HISTORY 자리 · H-0301 · H-0302 포함)
# AgentOps 첫 버전 등록 폴더 = 첫 태그의 pack/ 내용 + 옆에 ui-labels/(llmops kglink.pull과 같은 꼴)
rm -rf "$PACKS/first"; mkdir -p "$PACKS/first"
git -C "$P" archive --format=tar "refs/tags/$FIRST_TAG" pack | tar -x -C "$PACKS/first" --strip-components=1
git -C "$P" archive --format=tar "refs/tags/$FIRST_TAG" ui-labels | tar -x -C "$PACKS"
# 등록 화면의 실행 설정 목록은 execution/*.yaml만 보임 → 컨테이너용 하나를 execution/container.yaml로도 둠(원래 여섯 그대로 · revision 셈 밖 확인)
if [ -f "$EXEC_CFG" ]; then cp "$EXEC_CFG" "$PACKS/first/execution-config.yaml"; mkdir -p "$PACKS/first/execution"; cp "$EXEC_CFG" "$PACKS/first/execution/container.yaml"; else say "경고: AgentOps 실행 설정 없음($EXEC_CFG) — Pack의 execution-config.yaml 그대로(release_roots가 /packs 밖을 가리킬 수 있음)"; fi
ln -sfn /run/lmg/openai.env "$PACKS/first/.env"   # 키 파일은 agentops 컨테이너 tmpfs(start.sh) · 여기엔 링크만(값 0)
"$PY" -B -I "${EFF_PACKS:-/opt/efficiency-packs.py}" "$PACKS/first" "$PACKS" || stop 5 "효율 시나리오 출발 Pack 못 만듦"   # /packs/eff-model · /packs/eff-structure(효율 시나리오 출발 · 결과 영향 0 낭비 하나씩)
# 효율 출발 두 판을 Pack 저장소에도: 첫 태그에서 가지 eff-model · eff-structure → efficiency-packs.py와 같은 편집 → 커밋 · 태그(같은 이름)
# 배포 서버 전환 · 받아 넣기 도구의 바탕이 이 커밋이어야 함 · 끝에 그 커밋 pack/ revision = /packs/eff-* 등록 폴더 revision(다르면 멈춤 6)
"$PY" -B - "$P" "$PACKS" "${EFF_PACKS:-/opt/efficiency-packs.py}" "$FIRST_TAG" "$BRANCH" <<'PY' || stop 6 "효율 출발 커밋 · 태그 실패 또는 revision 다름"
import importlib.util, io, subprocess, sys, tarfile, tempfile
from pathlib import Path
from packrt.ir import revision
repo, packs, script, first, branch = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
spec = importlib.util.spec_from_file_location("efficiency_packs", script); eff = importlib.util.module_from_spec(spec); spec.loader.exec_module(eff)
def git(*a, raw=False):
    r = subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    return r.stdout if raw else r.stdout.decode().strip()
try:
    for name, edits in eff.EDITS.items():
        git("checkout", "-q", "-B", name, f"refs/tags/{first}")
        for f, old, new in edits:
            path = repo / "pack" / f; text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                sys.exit(f"{name}: pack/{f}에서 맞는 글 {text.count(old)}곳(1곳이어야)")
            path.write_text(text.replace(old, new), encoding="utf-8")
        git("-c", "user.name=kg-init", "-c", "user.email=kg-init@local", "commit", "-q", "-am", f"효율 시나리오 출발 {name}(첫 태그 {first} + 결과 영향 0 낭비 하나)")
        git("tag", name)
        with tempfile.TemporaryDirectory() as d:
            with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", f"refs/tags/{name}", "pack", raw=True))) as t:
                t.extractall(d, filter="data")
            a = revision.compute(Path(d) / "pack")[0]
        b = revision.compute(packs / name)[0]
        if a != b:
            sys.exit(f"{name}: 태그 revision {a[7:19]} ≠ 등록 폴더 {b[7:19]}")
        print(f"효율 출발 {name} · 커밋 {git('rev-parse', '--short', 'HEAD')} · 태그 {name} · revision {a[7:19]} = /packs/{name}")
finally:
    git("checkout", "-q", branch)   # 작업 가지는 첫 상태 그대로(pack-server · 감시가 보는 곳)
PY
printf 'AT=%s\nFIRST_PACK_TAG=%s\nPACK_REVISION=%s\nLAYER=%s\nROWS=%s\nPACK_COMMIT=%s\n' "$AT" "$FIRST_TAG" "$REV" "$LAYER" "$ROWS" "$(git -C "$P" rev-parse --short HEAD)" > "$LIVE/first-state.env"
echo "KG_INIT ok AT=$AT FIRST_PACK_TAG=$FIRST_TAG WATCH_BASE_TAG=${WATCH_TAG:-없음} PACK_REVISION=$REV LAYER=$LAYER ROWS=$ROWS"
