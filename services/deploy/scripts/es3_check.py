"""ES-3 — llmops 장부 줄 읽기 확인(V-04) → 상태 파일(registered / failed). 배포단은 장부에 쓰지 않는다(S-20).
EV-002 run 폴더 도구를 옮김(VFE EV-005 ES-B CHG-04): tag는 --tag(기본 v1), drs는 --drs(기본 drs:54e2537743f7fa9c) — v1 입력이면 원본과 같음(V-39).

V-04(읽기만): 설정의 배포 id 줄이 있고 pack_revision = 알림 revision, data_release_id = 예상 drs,
pack_commit = 알림 commit 40자 또는 빈 칸, pack_tag = 알림 tag(--tag, 기본 v1). 읽기 전후 `<장부>*` 곁 파일 목록을 관찰로 남긴다(SQLite 장부만).
장부 읽기는 서버와 같은 함수(deploy_server/registration.py ledger_rows — llmops_db SQLite · llmops_pg PG llmops.deployment, EV-005 ES-E).
통과 → scripts/deploy-status.py registered(V-04 통과 뒤에만, E3-A8 N3), 아니면 failed --detail(무엇이 없는지·다른지).
사용(배포단 루트): scripts/py scripts/es3_check.py --config C --id ID --version V1 --commit <40자> --revision <sha256:…> [--tag v1]
                     [--drs drs:…] [--status-dir D] [--no-status(대조만 · 상태 쓰기 0)]
종료: 0 registered / 1 failed 기록 / 2 사용 오류."""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from deploy_server.execution import ExecutionError  # noqa: E402
from deploy_server.registration import MODEL_COLUMN, LedgerUnavailable, ledger_dsn, ledger_name, ledger_rows, model_ok  # noqa: E402
from packrt.trace import model_release_id  # noqa: E402
from deploy_server.settings import load_settings  # noqa: E402

spec = importlib.util.spec_from_file_location("deploy_status", HERE / "scripts" / "deploy-status.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

EXPECTED_DRS = "drs:54e2537743f7fa9c"


def side_files(settings):
    """SQLite 장부 곁 파일 목록(관찰). PG 장부는 파일이 없어 None."""
    if settings.registration.get("mode") != "llmops_db":
        return None
    ledger = Path(settings.registration["path"])
    return sorted(p.name for p in ledger.parent.glob(ledger.name + "*"))


def check(settings, notice_commit, notice_revision, drs, tag="v1"):
    """V-04: (통과 여부, 이유 한 줄)."""
    if settings.expect_revision != notice_revision:
        return False, f"설정 expect_revision이 알림 revision과 다름({ds.short(settings.expect_revision)} / {ds.short(notice_revision)})"
    reg = settings.registration
    if reg.get("mode") not in ("llmops_db", "llmops_pg"):
        return False, f"등록 대조 설정이 아님(registration.mode {reg.get('mode')})"
    try:
        rows = ledger_rows(reg, settings.deployment_id, ledger_dsn(settings))
    except (LedgerUnavailable, ExecutionError) as e:
        return False, f"장부를 읽을 수 없음: {ledger_name(reg)}({type(e).__name__}{': ' + str(e) if isinstance(e, LedgerUnavailable) else ''})"
    if rows is None:
        return False, f"장부 없음: {ledger_name(reg)}"
    if not rows:
        return False, f"장부 줄 없음: {settings.deployment_id}(llmops가 아직 쓰지 않음 — 대신 쓰지 않음)"
    same_rev = [r for r in rows if r.get("pack_revision") == notice_revision]
    if not same_rev:
        got = ", ".join(ds.short(r.get("pack_revision")) for r in rows)
        return False, f"장부 줄 revision 다름: {settings.deployment_id} · 알림 {ds.short(notice_revision)} / 장부 {got}"
    same_drs = [r for r in same_rev if r.get("data_release_id") == drs]
    if not same_drs:
        return False, f"장부 줄 drs 다름: {same_rev[0].get('data_release_id')} / 예상 {drs}"
    mrs = model_release_id({k: v.get("release") for k, v in (settings.execution.get("models") or {}).items()})   # D-326 Q-B
    same_model = [r for r in same_drs if model_ok(r, mrs)]
    if not same_model:
        return False, f"장부 줄 모델 다름: {same_drs[0].get(MODEL_COLUMN)} / 이 설정 {mrs}"

    def problem(r):
        c = r.get("pack_commit")
        if c not in (None, "") and c != notice_commit:
            return f"장부 줄 commit이 알림 commit 40자가 아님: {c[:12]}({len(c)}자)"
        if r.get("pack_tag") != tag:
            return f"장부 줄 tag가 {tag}이 아님: {r.get('pack_tag')!r}"
        return None
    row = next((r for r in same_model if problem(r) is None), None)   # D-645: 같은 revision 줄이 여럿이면 칸이 모두 맞는 줄 하나면 됨(앞선 시도 줄이 먼저 걸리지 않게)
    if row is None:
        return False, problem(same_model[0])
    commit = row.get("pack_commit")
    return True, f"V-04 통과: {settings.deployment_id} · revision {ds.short(notice_revision)} · drs {drs} · commit {'40자 같음' if commit else '빈 칸'} · tag {tag}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--drs", default=EXPECTED_DRS)
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--status-dir")
    ap.add_argument("--no-status", action="store_true", help="대조만 · 상태 파일 쓰기 0(감시가 다시 물을 때 · D-645)")
    a = ap.parse_args(argv)
    if len(a.commit) != 40:
        print("사용 오류: --commit은 알림 commit 40자", file=sys.stderr)
        return 2
    s = load_settings(a.config)
    before = side_files(s)
    ok, why = check(s, a.commit, a.revision, a.drs, a.tag)
    after = side_files(s)
    if before is None:
        print(f"곁 파일(관찰): PG 장부({ledger_name(s.registration)}) — 해당 없음")
    else:
        print(f"곁 파일(관찰): 읽기 전 {before} / 뒤 {after} — {'같음' if before == after else '다름'}")
    print(why)
    if a.no_status:
        return 0 if ok else 1
    cmd = [str(HERE / "scripts" / "py"), str(HERE / "scripts" / "deploy-status.py"), "registered" if ok else "failed",
           "--id", a.id, "--version", a.version, "--config", a.config] + (["--status-dir", a.status_dir] if a.status_dir else [])
    if not ok:
        cmd += ["--detail", (ds.STEP3 + why)[:200]]   # 화면 ③ 글귀로 시작, 붙인 뒤 자름(EV-002 v12)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"deploy-status {'registered' if ok else 'failed'}: 종료 {r.returncode}")
    print(r.stdout.strip() or r.stderr.strip())
    if ok and r.returncode != 0:
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
