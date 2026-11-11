"""배포 상태 파일 — LLMOps 배포 승인 화면 3단계(배포단 등록)·4단계(운영 전환)용(지시 010, VFE EV-002 CHG-08).

status/deployments/<id>.json(git 무시)에 세 세션 공통 형식 한 개를 쓴다. LLMOps는 읽기만 한다.
  {"id","version","revision","stage","detail","commit","tag","deployment_id","updated_at"}
이 저장소가 쓰는 stage: registered · switched · failed (requested·applied·tagged는 task10-pack 몫).
새 객체가 아니라 기존 결과를 내보낸다: registered = llmops 장부 deployment 줄(읽기 전용 — llmops.db 또는 PG llmops.deployment,
deploy_server/registration.py의 ledger_rows로 서버와 같게, EV-005 ES-E), switched = 서버 실행 요약(RunStore).
서버 코드·등록 원장·trace는 바꾸지 않는다. 비밀 값은 읽지도 쓰지도 않는다.

사용(배포단 루트에서, scripts/py로):
  scripts/py scripts/deploy-status.py registered --id <change id> --version V1 --config config/v1-pg-local.yaml
  scripts/py scripts/deploy-status.py switched   --id <change id> --version V1 --config config/v1-pg-local.yaml --run-id <run_id>
  scripts/py scripts/deploy-status.py failed     --id <change id> --version V1 --config config/v1-pg-local.yaml --detail "<한 줄>"
  scripts/py scripts/deploy-status.py show       --id <change id>
  기본 폴더는 실제 등록 원장을 대조하는 설정에만 쓴다 — llmops.db(llmops-lite-v2/.llmops-data/llmops.db) 또는 PG 장부를 이 저장소 .env의
  runtime_reader로 읽는 설정(Mac *-pg-local, 답장 031-01). 예행연습(이름에 rehearsal)·시험 설정과 VPS 설정(/run/deploy/deploy.env — 늘
  --status-dir를 줌)은 --status-dir를 따로 준다.
종료 코드: 0 대조 통과·기록 / 1 대조 실패(failed 기록) / 2 사용 오류(아무것도 안 씀)."""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from deploy_server.execution import RUNTIME_DSN_ENV, ExecutionError  # noqa: E402
from deploy_server.registration import LedgerUnavailable, ledger_dsn, ledger_name, ledger_rows  # noqa: E402
from deploy_server.settings import SettingsError, load_settings  # noqa: E402
from deploy_server.stores import RunStore  # noqa: E402

DEFAULT_DIR = HERE / "status" / "deployments"
REAL_LLMOPS_DB = (HERE.parent / "llmops-lite-v2" / ".llmops-data" / "llmops.db").resolve()   # 실제 등록 원장 — 기본 폴더는 이것을 대조할 때만
REAL_DEPLOY_ENV = (HERE / ".env").resolve()   # PG 장부를 읽는 이 저장소 자체 .env(runtime_reader) — Mac 실제 PG 장부 판단(답장 031-01)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$")   # approved-change@1 id 패턴과 같음(파일 이름으로 안전)
VERSION_RE = re.compile(r"^V[0-9]+$")
FIELDS = ("id", "version", "revision", "stage", "detail", "commit", "tag", "deployment_id", "updated_at")
STEP3 = "배포단 등록 실패 — "   # 화면 배포 승인 창 ③ — llmops는 detail에 '전환'이 있으면 ④로 봄(EV-002 v12)
STEP4 = "운영 전환 실패 — "   # 화면 ④
DETAIL_MAX = 200


class UsageError(Exception):
    pass


def short(rev):
    return (rev or "").removeprefix("sha256:")[:8] or "-"


def status_path(status_dir, change_id):
    if not isinstance(change_id, str) or not ID_RE.fullmatch(change_id):
        raise UsageError(f"id 형식이 아닙니다(approved-change id 패턴): {change_id!r}")
    return Path(status_dir) / f"{change_id}.json"


def read_status(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise UsageError(f"상태 파일이 JSON이 아닙니다: {path.name}(고치거나 failed로 다시 쓰기 전에 확인)")


def write_status(path, record):
    """같은 폴더 임시 파일 → os.replace(LLMOps가 반쯤 쓴 파일을 읽지 않게)."""
    rec = {k: record.get(k) for k in FIELDS}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return rec


def uses_real_ledger(settings):
    """설정이 실제 등록 원장을 대조하는지(절대 경로 비교) — 기본 폴더 status/deployments는 이때만 쓴다.
    llmops_db: 장부 파일 = 실제 llmops.db. llmops_pg: 장부 DSN을 읽는 runtime_db.env_file = 이 저장소 .env(Mac 실제 PG 장부, 답장 031-01)이고
    환경 변수 LLMOPSAGENT_RUNTIME_DSN이 없을 때만(있으면 그 DSN이 먼저 쓰여 다른 DB일 수 있음 — 그때는 --status-dir를 줘야 함)."""
    reg = settings.registration
    if reg.get("mode") == "llmops_db":
        return Path(reg["path"]).resolve() == REAL_LLMOPS_DB
    if reg.get("mode") == "llmops_pg":
        env_file = settings.runtime_db.get("env_file")
        return bool(env_file) and Path(env_file).resolve() == REAL_DEPLOY_ENV and not os.environ.get(RUNTIME_DSN_ENV)
    return False


def base_record(args, settings):
    return {"id": args.id, "version": args.version, "revision": settings.expect_revision or None, "commit": None, "tag": None,
            "deployment_id": settings.deployment_id}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def cmd_registered(args, settings, path):
    rec = base_record(args, settings)
    reg = settings.registration
    if reg.get("mode") not in ("llmops_db", "llmops_pg"):
        return fail(path, rec, STEP3 + f"등록 대조 설정이 아닙니다(registration.mode {reg.get('mode')})")
    if not settings.expect_revision:
        return fail(path, rec, STEP3 + f"설정 expect_revision이 비어 있어 등록 줄을 대조할 수 없음: {settings.deployment_id}")
    try:
        rows = ledger_rows(reg, settings.deployment_id, ledger_dsn(settings))
    except (LedgerUnavailable, ExecutionError) as e:
        return fail(path, rec, STEP3 + f"등록 원장을 읽을 수 없음: {ledger_name(reg)}({type(e).__name__}{': ' + str(e) if isinstance(e, LedgerUnavailable) else ''})")
    if rows is None:
        return fail(path, rec, STEP3 + f"등록 원장이 없음: {ledger_name(reg)}")
    row = next((r for r in rows if r.get("pack_revision") == settings.expect_revision), None)
    if row is None:
        return fail(path, rec, STEP3 + f"등록 줄 없음: {settings.deployment_id} · revision {short(settings.expect_revision)}")
    rec.update(revision=row["pack_revision"], commit=row.get("pack_commit"), tag=row.get("pack_tag"), stage="registered",
               detail=f"배포단 등록 {settings.deployment_id} · revision {short(row['pack_revision'])} · drs {short(row.get('data_release_id', '').removeprefix('drs:'))} 대조")
    return ok(path, rec)


def cmd_switched(args, settings, path):
    rec = base_record(args, settings)
    prev = read_status(path)
    same_switch = prev and prev.get("stage") == "switched" and args.run_id in (prev.get("detail") or "")
    if not prev or not (prev.get("stage") == "registered" or same_switch) or prev.get("deployment_id") != settings.deployment_id:
        return fail(path, rec, STEP4 + f"registered 뒤에만 운영 전환을 기록함(지금: {(prev or {}).get('stage') or '파일 없음'})")
    rec.update(revision=prev.get("revision"), commit=prev.get("commit"), tag=prev.get("tag"))
    run = RunStore(settings.data_dir / "runs").get(args.run_id)
    if run is None:
        return fail(path, rec, STEP4 + f"실행 요약 없음: {args.run_id}")
    pack = run.get("pack") or {}
    problems = [name for name, good in (("deployment_id", run.get("deployment_id") == settings.deployment_id),
                                        ("revision", pack.get("pack_revision") == prev.get("revision")),
                                        ("tag", pack.get("tag") == prev.get("tag")),
                                        ("status", run.get("status") == "succeeded")) if not good]
    if problems:
        return fail(path, rec, STEP4 + f"운영 전환 대조 실패({', '.join(problems)}): {args.run_id}")
    rec.update(commit=pack.get("commit") or prev.get("commit"), stage="switched",
               detail=f"배포단이 태그 {pack.get('tag')}로 요청 처리 시작 — {args.run_id}")
    return ok(path, rec)


def cmd_failed(args, settings, path):
    detail = args.detail or ""
    if "\n" in detail or len(detail) > DETAIL_MAX or not detail.strip():
        raise UsageError(f"detail은 비어 있지 않은 한 줄·{DETAIL_MAX}자 이하입니다")
    if "://" in detail or "password" in detail.lower():
        raise UsageError("detail에 접속 정보처럼 보이는 글자(://, password)가 있어 쓰지 않습니다")
    rec = base_record(args, settings)
    prev = read_status(path)
    if prev and prev.get("deployment_id") == settings.deployment_id:
        rec.update(revision=prev.get("revision") or rec["revision"], commit=prev.get("commit"), tag=prev.get("tag"))
    return fail(path, rec, detail)


def ok(path, rec):
    rec["updated_at"] = now()
    out = write_status(path, rec)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def fail(path, rec, detail):
    rec.update(stage="failed", detail=detail, updated_at=now())
    out = write_status(path, rec)
    print(json.dumps(out, ensure_ascii=False))
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="배포 상태 파일(status/deployments/<id>.json) — 지시 010")
    ap.add_argument("command", choices=["registered", "switched", "failed", "show"])
    ap.add_argument("--id", required=True, help="승인된 변경 요청 id(approved-change@1 change_id)")
    ap.add_argument("--version", help="LLMOps Version 이름(예: V1)")
    ap.add_argument("--config", help="배포 설정 파일(config/*.yaml)")
    ap.add_argument("--run-id", help="switched: v1 첫 실행 run_id")
    ap.add_argument("--detail", help="failed: 멈춘 이유 한 줄")
    ap.add_argument("--status-dir", help=f"기본 {DEFAULT_DIR.relative_to(HERE)}")
    args = ap.parse_args(argv)
    try:
        status_dir = Path(args.status_dir) if args.status_dir else DEFAULT_DIR
        path = status_path(status_dir, args.id)
        if args.command == "show":
            rec = read_status(path)
            print(json.dumps(rec, ensure_ascii=False, indent=1) if rec else f"없음: {path}")
            return 0 if rec else 1
        if not args.version or not VERSION_RE.match(args.version):
            raise UsageError("--version은 V1 같은 Version 이름입니다")
        if not args.config:
            raise UsageError("--config가 필요합니다")
        if args.command == "switched" and not args.run_id:
            raise UsageError("switched에는 --run-id가 필요합니다")
        try:
            settings = load_settings(args.config)
        except (OSError, SettingsError, ValueError) as e:
            raise UsageError(f"설정을 읽을 수 없습니다: {type(e).__name__}: {str(e)[:200]}")
        if not args.status_dir and ("rehearsal" in Path(args.config).name or not uses_real_ledger(settings)):
            raise UsageError("실제 등록 원장을 대조하는 설정이 아니면(예행연습·시험) --status-dir를 따로 줘야 합니다(실제 status/deployments에 쓰지 않음)")
        return {"registered": cmd_registered, "switched": cmd_switched, "failed": cmd_failed}[args.command](args, settings, path)
    except UsageError as e:
        print(f"사용 오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
