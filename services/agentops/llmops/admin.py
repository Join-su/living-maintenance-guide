"""LLMOps admin commands (decision contract 6, answer 015-01 Q1).

  python -m llmops.admin deploy-bootstrap --deployment-id task10@v0-local --pack-dir <pack> --execution-config <config.yaml> \
      --decided-by owner-01 --reason "첫 배포 기준(93ca0a1, 수동 태그 v0)" [--pack-commit 93ca0a1] [--pack-tag v0] [--dev] [--data-dir <dir>]

  python -m llmops.admin data-release-auto --deployment-id <id> --pack-revision <rev> --drs <drs> --evidence <file | ->   (EV-044, R12)

  python -m llmops.admin index-builds [--check | --apply [--include-real]] [--data-dir <dir>]   (EV-051 W2-5, D-357)

  python -m llmops.admin gold-suite --project <id> --release <rid> --gold <hand-off node folder> [--inputs <Pack evals suite>]
      [--cards <file> --part work|confirm] [--check] [--data-dir <dir>]   (EV-051 R-5, D-372 · D-387)

Registers one deployment row (deployment_id, pack_revision, data_release_id, model_release_id — EV-044) together with its
decision(deploy_bootstrap) in one transaction of the Store (llmops.db, or the shared PostgreSQL llmops schema with LLMOPS_STORE=pg —
EV-029). The Pack is only read through packrt.ir (no Pack code runs here). The trace writer checks the row before it records a
deployment run (registration_check, three columns); the week-2 foreign key was withdrawn (EV-044 Q-G).
- reason and decided-by are required; the same key twice is refused (three columns before migration 0010, four after it)
- --dev takes only ids ending in @dev-local and stays open after the approval flow exists
- a production id is refused once its Pack has a deploy_approved decision (the bootstrap closes itself per Pack — EV-049; an approval
  whose Pack cannot be told closes every Pack)
- --pack-commit is stored as the full 40-character commit found in the Pack folder's git; not found → nothing registered (EV-021 CHG-105)
- the runtime DSN of a PostgreSQL execution config comes from the environment, else the DB env file's one line, like the server"""
import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from packrt import __version__ as runtime_version
from packrt import ir
from packrt.ir import releases
from packrt.trace import data_release_id

from . import deploying
from .config import REPO_ROOT, load_settings
from .store import StoreStartError, open_store


class BootstrapError(ValueError):
    pass


def full_commit(pack_dir, given):
    """The full 40-character commit for --pack-commit, read from the git the Pack folder is in (read only)."""
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", given or ""):
        raise BootstrapError(f"Pack 커밋 '{given}'은 commit hash 모양이 아닙니다")
    try:
        r = subprocess.run(["git", "-C", str(pack_dir), "rev-parse", "--verify", "--quiet", f"{given}^{{commit}}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BootstrapError(f"Pack 커밋 '{given}'를 확인할 수 없습니다 — {type(e).__name__}") from None
    full = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", full):
        raise BootstrapError(f"Pack 커밋 '{given}'를 찾을 수 없습니다(Pack 폴더의 git에 없거나, 여러 commit에 맞거나, git 밖)")
    return full


def runtime_env():
    """Where a PostgreSQL execution config finds its runtime DSN: this process's environment, else the DB env file
    (LLMOPS_DB_ENV_FILE, default db/.env) — that one line only, the server's rule (EV-021 CHG-105). Returns (env, dsn)."""
    from .packs import runtime_dsn
    env = dict(os.environ)
    if not env.get(releases.RUNTIME_DSN_ENV):
        dsn = runtime_dsn(load_settings().db_env_file)
        if dsn:
            env[releases.RUNTIME_DSN_ENV] = dsn
    return env, env.get(releases.RUNTIME_DSN_ENV)


def registration(pack_dir, config_path):
    """(pack_id, pack_revision, data_release_id, release_bindings, config_sha256, (model_release_id, model_bindings)) the same way
    packrt computes them at run time (EV-044 CHG-220: the config's models)."""
    res = ir.load_pack(Path(pack_dir))
    if not res.ok:
        raise BootstrapError("Pack을 불러올 수 없습니다 — " + " / ".join(e["message"] for e in res.errors)[:400])
    env, dsn = runtime_env()
    try:
        cfg = releases.load_execution_config(Path(config_path), res.doc, env=env)
    except releases.ReleaseError as e:
        msg = str(e)
        if dsn:   # never the DSN or its password in a message
            from urllib.parse import urlsplit
            for s in (dsn, urlsplit(dsn).password):
                msg = msg.replace(s, "[가림]") if s else msg
        raise BootstrapError(f"실행 설정을 확인할 수 없습니다 — {msg}") from None
    stores = cfg["bindings"]["stores"]
    bindings = {k: {"release_id": v.get("release_id"), "sha256": v.get("manifest_sha256")} for k, v in stores.items()}
    return (res.doc["pack"]["id"], res.revision, data_release_id({k: v.get("release_id") for k, v in stores.items()}), bindings,
            cfg["config_sha256"], deploying.model_line(cfg["bindings"].get("models")))


def deploy_bootstrap(store, *, deployment_id, pack_dir, execution_config, decided_by, reason, pack_commit=None, pack_tag=None, dev=False):
    if not (reason or "").strip() or not (decided_by or "").strip():
        raise BootstrapError("이유(--reason)와 결정자(--decided-by)는 반드시 적어야 합니다")
    if dev != deployment_id.endswith("@dev-local"):
        raise BootstrapError("--dev는 '@dev-local'로 끝나는 deployment_id에만, '@dev-local' id는 --dev로만 등록합니다")
    pack_id, revision, drs, bindings, config_sha, (mrs, mbind) = registration(pack_dir, execution_config)
    if pack_commit:
        pack_commit = full_commit(pack_dir, pack_commit)
    return store.add_deployment(deployment_id=deployment_id, pack_id=pack_id, pack_revision=revision, data_release_id=drs,
                                release_bindings=bindings, runtime_version=runtime_version, pack_commit=pack_commit, pack_tag=pack_tag,
                                config_sha256=config_sha, decided_by=decided_by.strip(), reason=reason.strip(), dev=dev,
                                model_release_id=mrs, model_bindings=mbind)


EVIDENCE_TEXT = ("store", "previous", "version", "corpus", "manifest_sha256", "config_sha256")


def data_release_auto(store, *, deployment_id, pack_revision, drs, evidence_path, stdin=None):
    """EV-044 CHG-222 (D-260 (1) · D-283 (3), deploy 101 Q-r2): the R12 '자료 바꿈' line the deploy side's r12-update asks for with
    `--ledger-cmd` — decision data_release_auto + one ledger row (Store.add_data_release_auto). The evidence file is the deploy
    side's {kind, reports, store, previous, version, corpus, drs} plus manifest_sha256 · config_sha256 (Q-C) and, when it knows,
    model_release_id (the release_line value that runs; null = a Pack without models). `-` reads the same JSON from stdin (D-328 덧:
    the deploy side calls it through `compose exec … --evidence -` on the VPS). Returns (row, written)."""
    if not (pack_revision or "").strip():
        raise BootstrapError("--pack-revision이 비었습니다(배포 쪽 expect_revision 없음)")
    try:
        text = (stdin or sys.stdin).read() if str(evidence_path) == "-" else Path(evidence_path).read_text(encoding="utf-8")
        ev = json.loads(text)
    except (OSError, ValueError) as e:
        raise BootstrapError(f"증거 파일을 읽을 수 없습니다 — {type(e).__name__}") from None
    if not isinstance(ev, dict) or ev.get("kind") != "data_release_auto":
        raise BootstrapError("증거 파일의 kind가 data_release_auto가 아닙니다")
    if ev.get("drs") != drs:
        raise BootstrapError(f"증거 파일의 drs({ev.get('drs')})가 --drs({drs})와 다릅니다")
    reports = ev.get("reports")
    if not isinstance(reports, list) or not reports or not all(isinstance(x, str) and x for x in reports):
        raise BootstrapError("증거 파일에 근거 보고서(reports)가 없습니다")
    if not (ev.get("manifest_sha256") and ev.get("config_sha256")):
        raise BootstrapError("증거 파일에 manifest_sha256 · config_sha256 없음(deploy Q-C)")
    bad = [k for k in EVIDENCE_TEXT if not isinstance(ev.get(k), str) or not ev[k]]
    if bad:
        raise BootstrapError(f"증거 파일 칸이 비었거나 글이 아닙니다: {', '.join(bad)}")
    given = "model_release_id" in ev
    if given and ev["model_release_id"] is not None and not re.fullmatch(r"mrs:[0-9a-f]{16}", str(ev["model_release_id"])):
        raise BootstrapError("증거 파일의 model_release_id가 mrs: 꼴이 아닙니다")
    return store.add_data_release_auto(deployment_id=deployment_id, pack_revision=pack_revision.strip(), data_release_id=drs,
                                       store_name=ev["store"], previous=ev["previous"], version=ev["version"],
                                       manifest_sha256=ev["manifest_sha256"], config_sha256=ev["config_sha256"], evidence=ev,
                                       model_given=given, model_release_id=ev.get("model_release_id"))


def wheel_carry_main(src, dst, src_pid, name, dst_pid, dst_base, src_data, dst_data, apply=False):
    """EV-090: check (default) or carry one copy wheel; prints the plan and, with apply, the record path. Returns the exit code."""
    from . import wheelcarry
    p = wheelcarry.plan(src, src_pid, name, dst, dst_pid, dst_base, src_data, dst_data)
    s = p.get("summary") or {}
    print(f"wheel-carry: {'옮김' if apply else '확인만'} — {name} → 받는 쪽 {s.get('name_next', '?')} · 줄 {json.dumps(s.get('rows', {}), ensure_ascii=False)}"
          f" · 폴더 {len(s.get('folders') or [])}")
    for e in p["errors"]:
        print(f"  멈춤: {e}", file=sys.stderr)
    if not p["ok"]:
        return 1
    if apply:
        rec = wheelcarry.apply(dst, p, dst_data, Path(dst_data) / "carry-records")
        print(f"  옮김 {rec['wheel']} · {rec['release']['name']} ({rec['release']['id']}) · 기록 {rec['path']}")
    return 0


def _db_env_dsn(path):
    """LLMOPS_APP_DSN from a DB env file — the value is never printed."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("LLMOPS_APP_DSN="):
            return line.split("=", 1)[1].strip()
    return None


def wheel_carry(a):
    from . import wheelcarry
    settings = load_settings()
    if a.data_dir:
        settings = dataclasses.replace(settings, data_dir=Path(a.data_dir))
    try:
        dst = open_store(settings)
    except StoreStartError as e:
        print(f"wheel-carry: 안 함 — 받는 쪽 저장소를 시작할 수 없습니다({e.kind})", file=sys.stderr)
        return 1
    if a.undo:
        rec = wheelcarry.undo(dst, a.undo, settings.data_dir)
        print(f"wheel-carry: 되돌림 — {rec['wheel']} · {rec['release']['name']} · Run {len(rec['runs'])} · 폴더 {len(rec['folders'])}")
        return 0
    if not (a.from_data and a.wheel and a.from_project and a.to_project and a.to_base):
        print("wheel-carry: --from-data · --wheel · --from-project · --to-project · --to-base가 필요합니다", file=sys.stderr)
        return 2
    from .store import Store
    src = Store(Path(a.from_data), pg_dsn=_db_env_dsn(a.from_db_env)) if a.from_db_env else Store(Path(a.from_data))
    return wheel_carry_main(src, dst, a.from_project, a.wheel, a.to_project, a.to_base, Path(a.from_data), settings.data_dir, apply=a.apply)


def knowhow_apply(a):
    """D-536 ⑥: each approved know-how of the file through llmops.knowhow.apply — fake extraction only (--answers: {text sha256: row}),
    the graph DB copy over HTTP (writes only llmops2-kh-* tags), the Pack's node through its own Python. Prints counts only."""
    from . import graphver, knowhow
    data = Path(a.data_dir)
    graph = graphver.HttpGraph(a.graph_url, Path(a.graph_env).expanduser())
    pack = knowhow.pack_runner(Path(a.pack_python), Path(a.kg), a.base)
    d = pack("decl", {})
    decl = {"rels": d["rels"], "causes": sorted(graph.causes(a.base)), "checks": graph.checks(a.base), "min_support": d["min_support"]}
    answers = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    env = dict(extract=knowhow.fake_extractor(answers), decl=decl, rules=d["rules"], graph=graph,
               versions=graphver.Versions(data / "graph-versions", base=a.base), pack=pack, cases=knowhow.cases_of(a.cases), records=data / "knowhow")
    for kh in json.loads(Path(a.knowhow).read_text(encoding="utf-8")):
        rec = knowhow.apply(kh, **env)
        print(json.dumps({"knowhow": rec["knowhow"], "status": rec["status"], "tag": rec.get("tag"), "weight": (rec.get("row") or {}).get("weight"),
                          "support": (rec.get("support") or {}).get("n"), "checks": rec.get("checks"), "why": rec.get("why"),
                          "groups": [{k: g[k] for k in ("group", "n", "first_before", "first_after", "changed", "wasted_before", "wasted_after")}
                                     for g in rec.get("groups") or []]}, ensure_ascii=False))
    print(json.dumps({"in_use": env["versions"].current()}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m llmops.admin")
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("deploy-bootstrap", help="배포 등록 한 줄(결정 deploy_bootstrap과 함께)")
    b.add_argument("--deployment-id", required=True)
    b.add_argument("--pack-dir", required=True)
    b.add_argument("--execution-config", required=True)
    b.add_argument("--decided-by", required=True)
    b.add_argument("--reason", required=True)
    b.add_argument("--pack-commit")
    b.add_argument("--pack-tag")
    b.add_argument("--dev", action="store_true")
    b.add_argument("--data-dir", help="llmops.db가 있는 데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data)")
    r = sub.add_parser("data-release-auto", help="R12 자료 바꿈 장부 줄(결정 data_release_auto, EV-044) — 배포 서버 r12-update가 부름")
    r.add_argument("--deployment-id", required=True)
    r.add_argument("--pack-revision", required=True)
    r.add_argument("--drs", required=True)
    r.add_argument("--evidence", required=True, help="증거 JSON 파일, 또는 - = stdin(D-328 덧)")
    r.add_argument("--data-dir", help="llmops.db가 있는 데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data)")
    m = sub.add_parser("move-to-pg", help="llmops.db 기록을 공유 PostgreSQL llmops로(EV-029 CHG-133, llmops_app)")
    mode = m.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="기본: 읽기만(줄 수 · pack_commit 옛 → 새 · 경로 표 미리보기)")
    mode.add_argument("--apply", action="store_true", help="대상 llmops 표가 모두 비었을 때만 한 트랜잭션으로 옮김")
    mode.add_argument("--reverse", action="store_true", help="PostgreSQL 기록을 새 data 폴더의 새 llmops.db로(--out)")
    m.add_argument("--out", help="--reverse의 새 data 폴더")
    m.add_argument("--data-dir", help="옛 llmops.db가 있는 데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data)")
    m.add_argument("--commit-map", help="task10-pack 이력 정리의 commit-map(옛 새)")
    m.add_argument("--pack-repo", help="태그를 rev-parse할 Pack 저장소(읽기만, 기본 ../task10-pack)")
    m.add_argument("--path-map", help="'옛 접두=새 접두' — --check에서 미리보기만")
    pm = sub.add_parser("path-map", help="기록 안 옛 경로 접두를 새 접두로(EV-VPS-01 CHG-V05, 서버와 같은 저장소)")
    pmode = pm.add_mutually_exclusive_group()
    pmode.add_argument("--check", action="store_true", help="기본: 읽기만(칸별 줄 수 · 예 하나 · 그대로 둔 추가만 칸)")
    pmode.add_argument("--apply", action="store_true", help="한 트랜잭션으로 바꾸고 데이터 폴더에 기록 파일")
    pmode.add_argument("--undo", metavar="RECORD", help="apply 기록 파일의 map을 거꾸로")
    pm.add_argument("--map", action="append", default=[], help="'옛 접두=새 접두'(여러 번)")
    pm.add_argument("--data-dir", help="데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data) — 기록 파일 자리, SQLite면 그 llmops.db")
    dm = sub.add_parser("data-manifest", help="데이터 폴더 파일 목록(경로 · 크기 · sha256, 읽기만) · 비교(EV-VPS-01 CHG-V06)")
    dm.add_argument("--data-dir", required=True)
    dm.add_argument("--out", help="목록을 쓸 파일(없으면 출력)")
    dm.add_argument("--compare", metavar="MANIFEST", help="다른 목록과 비교 — 차이가 있으면 종료 1")
    ib = sub.add_parser("index-builds", help="바퀴가 만든 벡터 DB version 폴더(<data dir>/index-builds) — 쓰지 않는 것 지우기(EV-051 W2-5)",
                        description="서버와 같은 계정으로 돌립니다. --apply는 서버 진단과 같은 잠금(<data dir>/.index-builds.lock) 안에서 보고 지웁니다 — "
                                    "서버를 이 코드로 다시 띄운 뒤 쓰기(옛 서버는 잠금을 잡지 않음).")
    imode = ib.add_mutually_exclusive_group()
    imode.add_argument("--check", action="store_true", help="기본: 읽기만(폴더 · 크기 · 쓰는 중 · 가짜/실제/모름 · 어느 바퀴)")
    imode.add_argument("--apply", action="store_true", help="쓰는 중이 아닌 가짜 임베딩 version 지움 — 서버 진단과 같은 잠금 안에서")
    ib.add_argument("--include-real", action="store_true", help="--apply 때 실제 임베딩 · mode 모름 version도 지움")
    ib.add_argument("--data-dir", help="서버와 같은 데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data) — LLMOPS_STORE=pg면 그 서버의 데이터 폴더인지 확인")
    gs = sub.add_parser("gold-suite", help="정답 suite 등록(Pack 저장소 밖 정답 폴더 + 입력, EV-051 R-5) — 같은 저장소 · 데이터 폴더",
                        description="정답 폴더(넘김 출력의 node 폴더 하나)를 그 release의 평가 suite로 등록합니다. 정답 폴더 · Pack 작업 폴더는 읽기만. "
                                    "서버 화면에서 suite를 등록하지 않는 때에 돌립니다(같은 내용 판정은 프로세스 하나 안 잠금).")
    gs.add_argument("--project", required=True)
    gs.add_argument("--release", required=True)
    gs.add_argument("--gold", required=True, help="넘김 출력의 node 폴더(suite.yaml · dataset · expectations · human-sample)")
    gs.add_argument("--inputs", help="정답 폴더 dataset의 입력이 비었을 때: Pack 작업 폴더 evals/ 안 입력 suite 이름")
    gs.add_argument("--cards", help="카드 id 목록 파일(줄마다 하나) — --part와 함께")
    gs.add_argument("--part", choices=("work", "confirm"))
    gs.add_argument("--check", action="store_true", help="쓰기 0: 맞춤 결과 · case 수 · node · store")
    gs.add_argument("--data-dir", help="서버와 같은 데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data)")
    wc = sub.add_parser("wheel-carry", help="사본에서 미리 돌린 바퀴 하나를 이 저장소로 — 그 버전이 '승인 대기'(EV-090 · D-495 ②)",
                        description="받는 쪽 = 서버와 같은 저장소(LLMOPS_STORE · 데이터 폴더). 기본은 확인만(쓰기 0). --apply는 한 트랜잭션 + 폴더 둘 + 기록 파일, "
                                    "--undo는 그 기록대로 되돌림. 비용 줄 · 진단 줄은 옮기지 않음.")
    wmode = wc.add_mutually_exclusive_group()
    wmode.add_argument("--apply", action="store_true", help="쓰기(덤프 먼저 · 오케 go)")
    wmode.add_argument("--undo", metavar="RECORD", help="apply 기록 파일대로 되돌림")
    wc.add_argument("--from-data", help="사본 데이터 폴더(그 llmops.db · edits · packs)")
    wc.add_argument("--from-db-env", help="사본이 PostgreSQL이면 그 DB env 파일(LLMOPS_APP_DSN 줄 · 값은 출력하지 않음)")
    wc.add_argument("--wheel", help="사본 바퀴 이름(A-<번호>)")
    wc.add_argument("--from-project")
    wc.add_argument("--to-project")
    wc.add_argument("--to-base", help="받는 쪽 기준 release id(사본 바퀴 기준과 같은 revision)")
    wc.add_argument("--data-dir", help="받는 쪽 데이터 폴더(기본: LLMOPS_DATA_DIR 또는 .llmops-data)")
    kh = sub.add_parser("knowhow-apply", help="승인된 노하우 → 관계 줄 → 시험용 그래프 버전 → 세 묶음 시험 → 추가 · 알림(D-536 ⑥ · 가짜 추출만)")
    kh.add_argument("--knowhow", required=True, help="[{id, text, report_id}] JSON 파일")
    kh.add_argument("--answers", required=True, help="가짜 추출 답 {글 sha256: 관계 줄} JSON 파일(AI 호출 0)")
    kh.add_argument("--cases", required=True, help="시험 사례 jsonl(kg-test cases 꼴 · 정답은 이쪽에만)")
    kh.add_argument("--kg", required=True, help="Pack의 kg-test 폴더(읽기 · 실행만)")
    kh.add_argument("--pack-python", required=True, help="Pack 그래프 venv의 python")
    kh.add_argument("--base", default="rel-v1", help="기준 관계 층 꼬리표(Pack 것 · 쓰기 0)")
    kh.add_argument("--graph-url", default="http://127.0.0.1:27474")
    kh.add_argument("--graph-env", default="~/.c1a15-kgdb/neo4j.env", help="NEO4J_AUTH 줄 env 파일(값 출력 0)")
    kh.add_argument("--data-dir", required=True, help="기록 폴더(graph-versions/ · knowhow/)")
    a = ap.parse_args(argv)
    if a.command == "knowhow-apply":
        return knowhow_apply(a)
    if a.command == "wheel-carry":
        return wheel_carry(a)
    if a.command == "gold-suite":
        return gold_suite(a)
    if a.command == "index-builds":
        return index_builds(a)
    if a.command == "move-to-pg":
        return move_to_pg(a)
    if a.command == "path-map":
        return path_map(a)
    if a.command == "data-manifest":
        return data_manifest(a)
    settings = load_settings()
    if a.data_dir:
        settings = dataclasses.replace(settings, data_dir=Path(a.data_dir))
    try:
        store = open_store(settings)   # EV-029 CHG-130: the same backend as the server (LLMOPS_STORE)
    except StoreStartError as e:
        print(f"{a.command}: 등록하지 않음 — 저장소를 시작할 수 없습니다({e.kind}: {e})", file=sys.stderr)
        return 1
    if a.command == "data-release-auto":
        try:
            row, written = data_release_auto(store, deployment_id=a.deployment_id, pack_revision=a.pack_revision, drs=a.drs, evidence_path=a.evidence)
        except (BootstrapError, ValueError, OSError) as e:
            print(f"data-release-auto: 쓰지 않음 — {e}", file=sys.stderr)
            return 1
        if not written:
            print("data-release-auto: 이미 있음 — 같은 줄이 장부에 있어 쓰지 않음", file=sys.stderr)
        print(json.dumps({**{k: row[k] for k in ("deployment_id", "pack_revision", "data_release_id", "model_release_id", "decision_id")},
                          "written": written}, ensure_ascii=False))
        return 0
    try:
        row = deploy_bootstrap(store, deployment_id=a.deployment_id, pack_dir=a.pack_dir, execution_config=a.execution_config,
                               decided_by=a.decided_by, reason=a.reason, pack_commit=a.pack_commit, pack_tag=a.pack_tag, dev=a.dev)
    except (BootstrapError, ValueError, OSError) as e:
        print(f"deploy-bootstrap: 등록하지 않음 — {e}", file=sys.stderr)
        return 1
    print(json.dumps({k: row[k] for k in ("deployment_id", "pack_id", "pack_revision", "data_release_id", "runtime_version", "decision_id")},
                     ensure_ascii=False))
    return 0


def move_to_pg(a):
    """EV-029 CHG-133. Prints a JSON summary; a stop prints the reason (deployment id · branch · table) and returns 1. No DSN or
    password is printed; the DSN is the DB env file's LLMOPS_APP_DSN line (llmops_app)."""
    from . import pgmove
    from .opsruns import app_dsn
    from .store import StoreStartError as StartError, pg_connect
    settings = load_settings()
    data_dir = Path(a.data_dir) if a.data_dir else settings.data_dir
    path_map = None
    if a.path_map:
        if not a.check and (a.apply or a.reverse):
            print("move-to-pg: 거부 — 경로 표는 --check 미리보기에서만 씁니다(--apply는 경로를 그대로 옮김)", file=sys.stderr)
            return 1
        if "=" not in a.path_map:
            print("move-to-pg: 거부 — --path-map은 '옛 접두=새 접두' 모양입니다", file=sys.stderr)
            return 1
        path_map = tuple(a.path_map.split("=", 1))
    try:
        if a.reverse:
            if not a.out:
                raise pgmove.MoveStop("--reverse에는 새 data 폴더 --out이 필요합니다")
            conn = pg_connect(app_dsn(settings.db_env_file) or "")
            try:
                target = pgmove.reverse(conn, a.out)
            finally:
                conn.close()
            print(json.dumps({"reverse": str(target), "tables": {t: len(r) for t, r in pgmove.read_old(target).items()}}, ensure_ascii=False))
            return 0
        old_db = data_dir / "llmops.db"
        if not old_db.is_file():
            raise pgmove.MoveStop(f"옛 llmops.db가 없습니다: {old_db}")
        commit_map = pgmove.read_commit_map(a.commit_map) if a.commit_map else {}
        rev_parse = pgmove.git_rev_parse(Path(a.pack_repo) if a.pack_repo else REPO_ROOT.parent / "task10-pack")
        if not a.apply:
            print(json.dumps({"check": pgmove.check(old_db, commit_map, rev_parse, path_map), "written": 0}, ensure_ascii=False, indent=1))
            return 0
        conn = pg_connect(app_dsn(settings.db_env_file) or "")
        try:
            record = pgmove.apply(old_db, conn, commit_map, rev_parse)
        finally:
            conn.close()
        print(json.dumps({"apply": {"tables": record["tables"], "pack_commit": record["pack_commit"]}, "moved_mark": str(data_dir / "llmops.db.moved-to-pg")},
                         ensure_ascii=False, indent=1))
        return 0
    except StartError as e:
        print(f"move-to-pg: 멈춤 — 저장소에 연결할 수 없습니다({e.kind}: {e})", file=sys.stderr)
        return 1
    except pgmove.MoveStop as e:
        print(f"move-to-pg: 멈춤 — {e}" + (f" [{e.deployment_id} · {e.branch}]" if e.deployment_id else ""), file=sys.stderr)
        return 1
    except Exception as e:   # a database error mid-move: the transaction rolled back; only its kind (no DSN, no row value)
        import psycopg
        if not isinstance(e, psycopg.Error):
            raise
        print(f"move-to-pg: 멈춤 — DB 오류({type(e).__name__}), 쓴 것 없음", file=sys.stderr)   # the kind only: a DB message can quote a row value
        return 1


def path_map(a):
    """EV-VPS-01 CHG-V05. Prints a JSON summary; a stop prints the reason and returns 1 (nothing written). No DSN or row value."""
    from . import pathmap
    settings = load_settings()
    if a.data_dir:
        settings = dataclasses.replace(settings, data_dir=Path(a.data_dir))
    if settings.store != "pg" and not (settings.data_dir / "llmops.db").is_file():   # never make an empty SQLite file (review VS-3 L-3)
        print(f"path-map: 멈춤 — {settings.data_dir / 'llmops.db'}가 없습니다(--data-dir 확인)", file=sys.stderr)
        return 1
    try:
        store = open_store(settings)
    except StoreStartError as e:
        print(f"path-map: 멈춤 — 저장소를 시작할 수 없습니다({e.kind}: {e})", file=sys.stderr)
        return 1
    try:
        if a.undo:
            out = pathmap.undo(store, a.undo, settings.data_dir)
        else:
            maps = pathmap.parse_maps(a.map)
            out = pathmap.apply(store, maps, settings.data_dir) if a.apply else pathmap.check(store, maps)
    except (pathmap.PathMapStop, OSError, ValueError) as e:   # a stop, or a record file that cannot be read: nothing written
        print(f"path-map: 멈춤 — {e}", file=sys.stderr)
        return 1
    except Exception as e:   # a database error: the transaction rolled back; only its kind (a DB message can quote a row value)
        if type(e).__module__.startswith(("psycopg", "sqlite3", "llmops.store")):
            print(f"path-map: 멈춤 — DB 오류({type(e).__name__}), 쓴 것 없음", file=sys.stderr)
            return 1
        raise
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def gold_suite(a):
    """EV-051 CHG-R5-1 (D-372 · D-387): register a gold suite — a hand-off node folder (and the Pack's inputs suite when its inputs
    are empty) — for one Agent Pack release, by the same registration rule as the screen. Prints one JSON; a refusal prints the
    reason and writes nothing."""
    import shutil
    import uuid
    from packrt.ir import load_pack
    from . import suites
    if bool(a.cards) != bool(a.part):
        print("gold-suite: 거부 — --cards와 --part는 함께 씁니다", file=sys.stderr)
        return 1
    settings = load_settings()
    if a.data_dir:
        settings = dataclasses.replace(settings, data_dir=Path(a.data_dir))
    if settings.store != "pg" and not (settings.data_dir / "llmops.db").is_file():
        print(f"gold-suite: 멈춤 — {settings.data_dir / 'llmops.db'}가 없습니다(--data-dir 확인)", file=sys.stderr)
        return 1
    try:
        store = open_store(settings)
    except StoreStartError as e:
        print(f"gold-suite: 멈춤 — 저장소를 시작할 수 없습니다({e.kind}: {e})", file=sys.stderr)
        return 1
    from . import guard
    store.redact = lambda s: guard.redact(s)   # every stored text masked first, as the server does (AC-26)
    rel = store.release(a.release)
    if not rel or rel.get("project_id") != a.project or not rel.get("pack"):
        print("gold-suite: 거부 — 그 Project의 Agent Pack release가 아닙니다", file=sys.stderr)
        return 1
    pack = rel["pack"]
    import tempfile
    # --check writes nothing in the data folder (its stage is a temporary folder); a registration stages beside the copies, as the screen does
    stage = Path(tempfile.mkdtemp(prefix="llmops-gold-")) / "stage" if a.check else settings.data_dir / "staging" / f"gold-{uuid.uuid4().hex[:8]}"
    try:
        doc = load_pack(Path(pack["copy"])).doc
        graph = (doc.get("graphs") or {}).get(pack.get("entrypoint")) or {}
        from packrt.ir import strict_yaml
        try:
            gmeta, _ = strict_yaml.load((Path(a.gold) / "suite.yaml").read_text(encoding="utf-8"))
        except strict_yaml.StrictYAMLError as e:
            raise suites.SuiteError(f"정답 suite.yaml {e}") from None
        lines0 = suites._jsonl_lines(Path(a.gold) / suites._plain((gmeta or {}).get("expectations") or "expectations.jsonl", "expectations"))
        if not lines0:
            raise suites.SuiteError("expectations가 비어 있습니다")
        exp0 = json.loads(lines0[0])
        if not isinstance(exp0, dict) or not isinstance(exp0.get("nodes"), dict):
            raise suites.SuiteError("expectations 줄 모양이 다릅니다")
        node = next(iter(exp0.get("nodes") or {}), None)
        n = next((x for x in graph.get("nodes", []) if x.get("id") == node), None)
        if not n or n.get("kind") != "retrieve@2":
            raise suites.SuiteError(f"정답 node '{node}'가 이 release 그래프({pack.get('entrypoint')})의 retrieve@2 node가 아닙니다")
        store_name = (n.get("params") or {}).get("store")
        cards = [ln.strip() for ln in Path(a.cards).read_text(encoding="utf-8").splitlines() if ln.strip()] if a.cards else None
        if a.inputs and (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", a.inputs) or ".." in a.inputs):
            raise suites.SuiteError("--inputs는 Pack 작업 폴더 evals/ 안 suite 이름 하나입니다")
        inputs = Path(pack["work_folder"]) / "evals" / a.inputs if a.inputs else None
        name, gold = suites.gold_stage(a.gold, stage, node, store_name, inputs, cards, a.part)
        review_nodes = [x["name"] for x in (rel.get("graph") or {}).get("nodes", []) if str(x.get("kind", "")).startswith("human_review")]
        su = suites.read_suite(stage, name, pack, review_nodes)
        su.pop("source")
        su["gold"] = gold
        out = {"suite": su["name"], "folder": name, "node": node, "store": store_name, "cases": len(su["cases"]),
               "human_sample": sum(1 for c in su["cases"] if c.get("human_sample")), "suite_revision": su["suite_revision"], "gold": gold}
        if a.check:
            print(json.dumps({**out, "written": 0}, ensure_ascii=False, indent=1))
            return 0
        rec = suites.register_copy(store, settings.data_dir, a.project, su, stage / "evals" / name)
        print(json.dumps({**out, "id": rec["id"]}, ensure_ascii=False, indent=1))
        return 0
    except (suites.SuiteError, OSError, ValueError, KeyError, StopIteration, TypeError, AttributeError, suites.strict_yaml.StrictYAMLError) as e:
        print(f"gold-suite: 거부 — {e if isinstance(e, suites.SuiteError) else type(e).__name__ + ': ' + str(e)[:200]}, 쓴 것 없음", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(stage.parent if a.check else stage, ignore_errors=True)


def index_builds(a):
    """EV-051 CHG-W2-28 (D-357): what is under <data dir>/index-builds and, with --apply, removing what nothing uses. The admin
    command cannot see the server's running rows, so an ended wheel not swept yet keeps every row's version. Prints one JSON."""
    from . import packs
    if a.include_real and not a.apply:
        print("index-builds: 거부 — --include-real은 --apply와 함께만", file=sys.stderr)
        return 1
    settings = load_settings()
    if a.data_dir:
        settings = dataclasses.replace(settings, data_dir=Path(a.data_dir))
    if settings.store != "pg" and not (settings.data_dir / "llmops.db").is_file():   # never make an empty SQLite file
        print(f"index-builds: 멈춤 — {settings.data_dir / 'llmops.db'}가 없습니다(--data-dir 확인)", file=sys.stderr)
        return 1
    try:
        store = open_store(settings)
    except StoreStartError as e:
        print(f"index-builds: 멈춤 — 저장소를 시작할 수 없습니다({e.kind}: {e})", file=sys.stderr)
        return 1
    data_dir = settings.data_dir

    def look():
        unswept = [w["wheel_id"] for w in store.ended_wheels() if not store.wheel_state(w["wheel_id"]).get(packs.SWEPT)]
        used, why = packs.index_builds_in_use(data_dir, store, unswept)
        at5 = sum(1 for w in store.open_wheels() if w["cell"] == 5)   # shown only (W2-5b (가): the lock replaces the refusal)
        return used, why, at5

    lock = None
    if a.apply:   # EV-051 CHG-W2-30: look and remove inside the diagnosis pass's lock
        if os.stat(data_dir).st_uid != os.getuid():   # another account would own the lock file and the server would lose it (N-W25B-5)
            print("index-builds: 거부 — 데이터 폴더 주인과 다른 계정입니다(서버와 같은 계정으로), 지운 것 없음", file=sys.stderr)
            return 1
        try:
            lock = packs.take_index_lock(data_dir, packs.LOCK_WAIT_ADMIN)
        except packs.LockBusy:
            print(f"index-builds: 거부 — 서버 진단이 잠금을 놓지 않음({packs.LOCK_WAIT_ADMIN}초), 지운 것 없음 — 진단이 끝난 뒤 다시", file=sys.stderr)
            return 1
        except (packs.LockUnusable, OSError) as e:
            print(f"index-builds: 거부 — 잠금 파일을 쓸 수 없음({e if isinstance(e, packs.LockUnusable) else type(e).__name__}), 지운 것 없음", file=sys.stderr)
            return 1
    try:
        return _index_builds(a, store, data_dir, look)
    finally:
        if lock is not None:
            packs.release_index_lock(lock)


def _index_builds(a, store, data_dir, look):
    from . import packs

    def removable(v):
        return v["mode"] == "fake" or a.include_real

    used, why, at5 = look()
    try:
        versions = packs.index_build_versions(data_dir)
    except (ValueError, OSError) as e:
        print(f"index-builds: 멈춤 — {e if isinstance(e, ValueError) else type(e).__name__}", file=sys.stderr)
        return 1
    rows = {}
    for p in store.projects():
        for w in store.wheels(p["id"]):
            for r in store.candidates(w["wheel_id"]):
                for rid in packs._row_builds(r):
                    rows.setdefault(rid, f"{p['name']} A-{w['seq']}")
    try:   # D-362 (4): the row copies the server removes by itself (ended wheels, wheels gone) — shown, never written here
        ended = {w["wheel_id"] for w in store.ended_wheels()}
        copies = {wid: b for wid, b in packs.row_copies(data_dir).items() if wid in ended or store.wheel(wid) is None}
        rows_note = {"count": len(copies), "bytes": sum(copies.values()), "wheels": sorted(copies)}
    except (ValueError, OSError) as e:
        rows_note = {"count": None, "bytes": None, "blocked": str(e) if isinstance(e, ValueError) else type(e).__name__}
    out = {"folder": str(data_dir / packs.INDEX_BUILDS),
           "versions": [{"id": v["id"], "mode": v["mode"] or "모름", "bytes": v["bytes"], "in_use": None if why else v["id"] in used,
                         "used_by": used.get(v["id"], []), "wheel": rows.get(v["id"]), "skip": v["skip"]} for v in versions],
           "unused_bytes": None if why else sum(v["bytes"] for v in versions if v["id"] not in used and not v["skip"]), "blocked": why, "wheels_at_5": at5,
           "row_copies": rows_note}
    if not a.apply:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if why:
        print(f"index-builds: 거부 — 쓰는 중인지 알 수 없음({why}), 지운 것 없음", file=sys.stderr)
        return 1

    def still_unused(rid):   # again just before each removal, inside the lock (a request thread may have changed a copy)
        used2, why2, _ = look()
        return not why2 and rid not in used2

    gone = packs.drop_index_builds(data_dir, [v["id"] for v in versions if v["id"] not in used and not v["skip"] and removable(v)], still_unused)
    print(json.dumps({**out, "removed": gone["removed"], "kept": gone["kept"]}, ensure_ascii=False, indent=1))
    return 0


def data_manifest(a):
    """EV-VPS-01 CHG-V06. Read only."""
    from . import datamanifest
    try:
        doc = datamanifest.build(a.data_dir)
        if a.out:
            Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if a.compare:
            diff = datamanifest.compare(doc, datamanifest.load(a.compare))
            print(json.dumps({"files": len(doc["files"]), **diff}, ensure_ascii=False, indent=1))
            return 1 if any(diff.values()) else 0
        if not a.out:
            print(json.dumps(doc, ensure_ascii=False, indent=1))
        else:
            print(json.dumps({"files": len(doc["files"]), "out": a.out}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as e:
        print(f"data-manifest: 멈춤 — {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
