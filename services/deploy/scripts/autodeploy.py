"""배포 승인 뒤 ② ~ ④ 자동 감시(D-455 · 저장소로 옮김 D-470, VFE EV-005 AUTODEPLOY-REPO-E1) — 맥 · 감시 하나 = 배포 id 하나(설정의 deployment_id).
LLMOps 요청 폴더(approved-change@2)를 2초마다 보고, 켠 뒤 새로 생긴 CHG-c1a15- 파일 하나마다:
  ② c1a15-pack 받아 넣기 도구 dry-run → 통과 때만 --apply(--by 자동 감시) → Pack 상태 tagged
  → 장부 줄(LLMOps가 그 Project 화면 읽을 때 씀)을 서버와 같은 ledger_rows로 기다림 → 설정 ref · expect 바꿈(커밋 0) → ③ es3_check registered
  → ④ r12-update check → 그 포트의 이 저장소 서버 pid만 TERM → serve.sh → /info → C01 1건 → switched
실패 = 그 단계 failed(② Pack 도구 · ③ ④ deploy-status) · 자동 다시 0 · 같은 change id 두 번 0 · dry_run 파일 무시 · 끄기는 단계 사이에서만.
비밀 값 · DSN · provider 오류 글 출력 0(code · where · HTTP만). push 0 · LLMOps 쓰기 0.
사용(배포단 루트): scripts/py scripts/autodeploy.py start|stop|status|run|once
값(env): AD_PACK_TOOL(기본 <AD_PACK_REPO>/tools/change/receive_approved.py — c1a15-pack 013-76, 없으면 켜지 않음) · AD_CONFIG(config/c1a15-dev-local.yaml) · AD_PORT(8796) · AD_SERVE_LOG
         · AD_REQ_DIR · AD_PACK_REPO · AD_PACK_PY · AD_BRANCH(demo-nano) · AD_ACCEPT(0.12.6) · AD_C01_REF(v7) · AD_STATE · AD_WAIT(180) · AD_POLL(2)
         · AD_AUTO_APPLY(off · dev-2만 on) · AD_LLMOPS_URL(http://127.0.0.1:8766 — 결정자 읽기)
자동 승인(D-565 · dev-2만): LLMOps가 스스로 낸 배포 승인 요청(결정자 auto-apply · LLMOps /api/state 버전 rollout)은 AD_AUTO_APPLY=on일 때만 처리,
off(기본)면 처리 0 · 배포 상태 failed('자동 승인 꺼짐 — 사람 승인 필요'). 사람이 결정한 요청 · 결정자를 못 읽은 요청은 그대로.
KG(D-570 · dev-2 시연만): AD_KG=on이면 같은 고리가 KG_INBOX(KG 반영 대기 폴더 = 로그 · 앱 승인 기록 파일 · KG_LETTERS off가 아닐 때)
· KG_CANDIDATES(kg-candidate@1)에 처리 표시 없는 파일이 보일 때
scripts/kg_apply.py를 한 번 부름(환경 그대로 · 같은 묶음이 실패했으면 묶음이 바뀔 때까지 다시 0). 버전 전환(handle)은 kg_apply와 같은 잠금
(KG_STATE/kg-apply.lock) 안에서 — 한 서버를 둘이 동시에 다시 띄우지 않게. AD_VERSIONS=off(시연 기간 버전 전환 0)면 요청 폴더 · 받아 넣기 도구를 보지 않음.
되돌리기(D-633 · dev-2 시연만): AD_ROLLBACK=on이면 같은 요청 폴더의 되돌리기 요청(llmops/rollback-request@1 · RB- id · LLMOps 되돌리기 승인이 씀)
하나마다 → 대조는 revision: 그 revision이 이미 떠 있으면 다시 띄움 0 · registered → switched, 아니면 그 revision의 태그(요청 tag 또는 장부 줄)
· 태그 revision 대조 → 상태 파일 registered → graph_swap.swap(설정 두 칸 · 그 서버만 다시 띄움 ·
실패하면 옛 설정 · 옛 서버) → /info revision · drs 대조 → switched. C01 Run(실제 호출) 0 · 배운 예외 옮겨 싣기 0(앞 버전으로 돌아감) · 같은 id 두 번 0 ·
상태 파일이 있으면 손 0 · 켤 때 있던 파일 손 0 · 이 감시 배포 id가 아닌 요청 손 0. 기본 off(운영 · dev 감시 그대로)."""
import fcntl
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy_server.execution import ExecutionError  # noqa: E402
from deploy_server.registration import LedgerUnavailable, ledger_dsn, ledger_rows  # noqa: E402
from deploy_server.settings import load_settings  # noqa: E402

CFG = os.environ.get("AD_CONFIG") or "config/c1a15-dev-local.yaml"
PORT = int(os.environ.get("AD_PORT") or 8796)
URL = f"http://127.0.0.1:{PORT}"
LOG = Path(os.environ.get("AD_SERVE_LOG") or ROOT / "var" / "logs" / "c1a15-dev-local-v7-0125.log")   # 지금 8796 서버 로그(이어 씀)
REQ = Path(os.environ.get("AD_REQ_DIR") or ROOT.parent / "llmops-lite-v2" / ".llmops-data" / "approved-changes")
PACK = Path(os.environ.get("AD_PACK_REPO") or ROOT.parent / "c1a15-pack")
TOOL = Path(os.environ.get("AD_PACK_TOOL") or PACK / "tools" / "change" / "receive_approved.py")   # E1 Q1 (가) → c1a15-pack 013-76: 저장소 도구가 기본
PY = os.environ.get("AD_PACK_PY") or str(PACK / ".venv" / "bin" / "python")
BRANCH = os.environ.get("AD_BRANCH") or "demo-nano"
ACCEPT = os.environ.get("AD_ACCEPT") or "0.12.6"   # 설정 고정값(D-451) — 요청 파일 packrt 값을 넘기지 않음
C01_REF = os.environ.get("AD_C01_REF") or "v7"     # C01 예시(contracts/v4/examples/start.request.json)를 읽을 Pack 태그
BY = "자동 감시"
STATE = Path(os.environ.get("AD_STATE") or ROOT / "var" / "autodeploy")
WAIT = float(os.environ.get("AD_WAIT") or 180)
POLL = float(os.environ.get("AD_POLL") or 2)
ES3_WAIT = float(os.environ.get("AD_ES3_WAIT") or 30)   # D-645: ③ 앞 es3 대조를 상태 쓰기 없이 다시 묻는 시간(장부 줄을 본 같은 초에 대조가 실패하던 꼴)
_AUTO = os.environ.get("AD_AUTO_APPLY") or "off"
if _AUTO not in ("on", "off"):
    raise SystemExit("AD_AUTO_APPLY는 비워 두거나 on · off입니다")
AUTO_APPLY = _AUTO == "on"   # D-565: dev-2 감시만 on — 운영 · dev 감시는 기본 off 그대로
LLMOPS = (os.environ.get("AD_LLMOPS_URL") or "http://127.0.0.1:8766").rstrip("/")
AUTO_BY = "auto-apply"       # LLMOps D-565 AUTO_BY(자동 개선이 스스로 낸 배포 승인의 결정자)
_KG = os.environ.get("AD_KG") or "off"
_VERSIONS = os.environ.get("AD_VERSIONS") or "on"
if _KG not in ("on", "off") or _VERSIONS not in ("on", "off"):
    raise SystemExit("AD_KG · AD_VERSIONS는 비워 두거나 on · off입니다")
_RB = os.environ.get("AD_ROLLBACK") or "off"
if _RB not in ("on", "off"):
    raise SystemExit("AD_ROLLBACK은 비워 두거나 on · off입니다")
ROLLBACK = _RB == "on"           # D-633: 되돌리기 요청 받기(dev-2 시연만)
KG = _KG == "on"                 # D-570: 후보 그래프 반영(dev-2 시연만) — 운영 · dev 감시는 off 그대로
VERSIONS = _VERSIONS == "on"     # off = 시연 기간 버전 전환 0
KG_CANDS = Path(os.environ["KG_CANDIDATES"]) if os.environ.get("KG_CANDIDATES") else None
KG_STATE = Path(os.environ["KG_STATE"]) if os.environ.get("KG_STATE") else None
KG_INBOX = Path(os.environ["KG_INBOX"]) if os.environ.get("KG_INBOX") else None
KG_LETTERS = (os.environ.get("KG_LETTERS") or "apply") != "off"   # kg_apply와 같은 칸 — 시연(D-570 23:46) = 대기 폴더가 로그 · 배포가 보고 관문 → 반영
PREFIX = "CHG-c1a15-"
FORMAT = "task10-pack/approved-change@2"
RB_PREFIX = "RB-"
RB_FORMAT = "llmops/rollback-request@1"
STATUS_FIELDS = ("id", "version", "revision", "stage", "detail", "commit", "tag", "deployment_id", "updated_at")   # deploy-status.py와 같은 9칸
STOP = False
TOLD = set()   # 다시 쓴 파일 알림 한 번만


class Stop(Exception):
    """이 건은 여기서 멈춤(상태 파일은 이미 씀)."""


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def on_term(*_):
    global STOP
    STOP = True


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]{6,}|(?i:bearer)\s+\S+|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)|(?i:password|passwd|token|api[_-]?key)\s*[=:]\s*\S+")


def say_stderr(r, n=3):
    """실패한 도구의 stderr 끝 줄을 감시 로그에(D-687 — capture 뒤 버려져 V60 ② 실패 원인을 못 찾았음). 키 · DSN 비밀번호 모양은 가림 · 한 줄 300자."""
    if r.returncode:
        for line in (r.stderr or "").strip().splitlines()[-n:]:
            say("   stderr:", SECRET_RE.sub("<가림>", line)[:300])


def last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {}


# ── 서버 손(포트 주인 · /info · C01 · 밖 연결) ──

def get(path, timeout=5):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return json.loads(r.read())


def listener():
    """그 포트를 듣는 pid 하나. 이 저장소의 deploy_server가 아니면 멈춤(남의 프로세스 손 0)."""
    pids = sh(["lsof", "-nP", "-t", f"-iTCP:{PORT}", "-sTCP:LISTEN"]).stdout.split()
    if not pids:
        return None
    if len(pids) != 1:
        raise SystemExit(f"멈춤: {PORT} 듣는 프로세스가 {len(pids)}개")
    pid = pids[0]
    cmd = sh(["ps", "-o", "command=", "-p", pid]).stdout.strip()
    cwd = next((l[1:] for l in sh(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"]).stdout.splitlines() if l.startswith("n")), "")
    if "deploy_server.app" not in cmd or f"--port {PORT}" not in cmd or cwd != str(ROOT):
        raise SystemExit(f"멈춤: {PORT} 주인이 이 저장소의 deploy 서버가 아님(pid {pid}) — 손 0")
    return pid


def info_line():
    i = get("/info")
    p = i.get("pack") or {}
    return {"tag": p.get("tag"), "revision": (p.get("pack_revision") or "")[:15], "runtime": i.get("runtime_version"),
            "embedding": (i.get("provider") or {}).get("embedding")}, p.get("pack_revision")


def post_start():
    """C01 start 1건 → run_id. 201 아니면 code · where · HTTP만(message 글 출력 0) 남기고 SystemExit."""
    body = sh(["git", "-C", str(PACK), "show", f"{C01_REF}:contracts/v4/examples/start.request.json"], check=True).stdout
    req = urllib.request.Request(URL + "/runs", data=body.encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Idempotency-Key": f"ad-{time.time_ns()}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            d = json.loads(resp.read())
            say("⑤ C01 start", resp.status, d.get("run_id"), d.get("status"))
            return d.get("run_id")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error") or {}
        except Exception:
            err = {}
        say("⑤ C01 start HTTP", e.code, "· code", err.get("code"), "· where", err.get("where"))
        raise SystemExit("멈춤: C01 201 아님")


def outbound(pid):
    rows = sh(["lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:ESTABLISHED"]).stdout.splitlines()[1:]
    far = sorted({r.split()[8].split("->")[1].rsplit(":", 1)[0] for r in rows if "->" in r} - {"127.0.0.1", "[::1]"})
    try:
        oai = sorted({a[4][0] for a in socket.getaddrinfo("api.openai.com", 443)})
    except OSError:
        oai = []
    say("밖 연결", far, "· api.openai.com 주소", oai, "· 그 밖", sorted(set(far) - set(oai)))


# ── 설정 · 장부 ──

def cfg_state(text):
    ref = next((l.split()[1] for l in text.splitlines() if l.strip().startswith("ref:")), None)
    exp = next((l.split('"')[1] for l in text.splitlines() if "expect_revision:" in l), None)
    return ref, exp


def ledger():
    """설정 배포 id의 장부 줄 → (pack_tag, pack_revision, data_release_id) 목록. 서버 · es3_check와 같은 ledger_rows(runtime_reader · 읽기 전용).
    닿지 못하면 빈 목록(형 이름만 로그, DSN 출력 0)."""
    s = load_settings(ROOT / CFG)
    try:
        rows = ledger_rows(s.registration, s.deployment_id, ledger_dsn(s)) or []
    except (LedgerUnavailable, ExecutionError) as e:
        say("   장부 읽기 실패", type(e).__name__)
        return []
    return [(r.get("pack_tag"), r.get("pack_revision"), r.get("data_release_id")) for r in rows]


def ledger_row(rev, want_tag=None):
    """revision이 같은 첫 장부 줄 → (tag, drs) 또는 None. want_tag면 그 태그 줄만(D-645: ② 태그까지 찬 줄)."""
    for tag, r, drs in ledger():
        if r == rev and (want_tag is None or tag == want_tag):
            return tag, drs
    return None


def set_config(tag, rev):
    p = ROOT / CFG
    s = p.read_text()
    ref, exp = cfg_state(s)
    if (ref, exp) == (tag, rev):
        return "같음"
    a = f"  ref: {ref} "
    if s.count(a) != 1 or s.count(exp) != 1:
        raise ValueError(f"설정 ref {ref} · expect 자리가 하나가 아님")
    p.write_text(s.replace(a, f"  ref: {tag} ").replace(exp, rev))
    return f"{ref} → {tag}"


# ── 한 건 ──

def status_dir():
    """상태 파일 폴더 하나(LLMOps가 읽는 곳 · 되돌리기 상태 파일도 같은 곳) — AD_STATUS_DIR 또는 <ROOT>/status/deployments.
    D-645(packrt-1 232): deploy-status · es3_check는 실제 원장(박아 둔 llmops.db)이 아닌 설정이면 --status-dir 없이 거절(종료 2) → 늘 넘김."""
    return Path(os.environ.get("AD_STATUS_DIR") or ROOT / "status" / "deployments")


def deploy_status(cmd, cid, version, **kw):
    args = ["scripts/py", "scripts/deploy-status.py", cmd, "--id", cid, "--version", version, "--config", CFG, "--status-dir", str(status_dir())]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", v]
    r = sh(args, cwd=ROOT)
    say(f"   deploy-status {cmd}", r.returncode, (last_json(r.stdout).get("stage") or (r.stderr.strip().splitlines() or [""])[-1][:120]))
    return r.returncode


def failed(cid, version, detail):
    deploy_status("failed", cid, version, detail=detail[:200])
    raise Stop(detail)


def check_stop(cid, version):
    if STOP:
        failed(cid, version, "감시 꺼짐 — 사람이 이어서(s3go)")


def has_by():
    try:
        return "--by" in TOOL.read_text()
    except (OSError, AttributeError):
        return False


def pack_tool(req, apply):
    cmd = [PY, "-B", str(TOOL), str(req), "--repo", str(PACK), "--branch", BRANCH, "--update-start-expectations", "--packrt-accept", ACCEPT]
    if has_by():
        cmd += ["--by", BY]
    if apply:
        cmd.append("--apply")
    r = sh(cmd, cwd=str(PACK))
    j = last_json(r.stdout)
    say(f"② {'apply' if apply else 'dry-run'} exit", r.returncode, "·", {k: j.get(k) for k in ("result", "stage", "tag") if k in j})
    say_stderr(r)
    return r.returncode, j


def pack_status(cid):
    try:
        return json.loads((PACK / "status" / "approved-changes" / f"{cid}.json").read_text())
    except (OSError, ValueError):
        return {}


def switch(cid, version, tag, rev, drs):
    """④ 이미 같은 revision이 떠 있으면 다시 띄움 0 → C01 → switched."""
    pid = listener()
    now = info_line()[1] if pid else None
    if now != rev:
        r = sh(["scripts/py", "scripts/r12-update", "check", "--config", CFG], cwd=ROOT)
        say("④ r12-update check", r.returncode)
        say_stderr(r)
        if r.returncode:
            failed(cid, version, "운영 전환: 묶기 대조 실패 — 서버 손 0")
        check_stop(cid, version)
        t0 = time.time()
        if pid:
            subprocess.run(["kill", "-TERM", pid])
            for _ in range(50):
                if not sh(["lsof", "-nP", "-t", f"-iTCP:{PORT}", "-sTCP:LISTEN"]).stdout.strip():
                    break
                time.sleep(0.2)
            else:
                failed(cid, version, f"운영 전환: {PORT}이 10초 안에 비지 않음")
        subprocess.Popen(["nohup", "scripts/serve.sh"], cwd=ROOT, env={**os.environ, "DEPLOY_CONFIG": CFG, "DEPLOY_PORT": str(PORT)},
                         stdout=open(LOG, "a"), stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):
            try:
                get("/health", 1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            failed(cid, version, "운영 전환: 서버가 20초 안에 뜨지 않음 — 되돌림 s3back")
        line, now = info_line()
        say(f"④ 다시 띄움 {time.time() - t0:.1f}초 · pid {listener()} · /info", line)
    else:
        say("④ 이미 같은 revision이 떠 있음 — 다시 띄움 0")
    got_drs = get("/info").get("data_release_id")
    if now != rev or got_drs != drs:
        failed(cid, version, "운영 전환: /info revision · drs가 기대와 다름 — 되돌림 s3back")
    try:
        rid = post_start()
    except SystemExit:
        failed(cid, version, "운영 전환: C01 시작 요청이 201 아님")
    outbound(listener())
    if deploy_status("switched", cid, version, run_id=rid):
        raise Stop("switched 기록 실패")
    return rid


def llmops_state():
    with urllib.request.urlopen(LLMOPS + "/api/state", timeout=60) as r:
        return json.loads(r.read())


def decided_by(cid):
    """그 change id의 배포 승인 결정자(LLMOps /api/state 버전 rollout · 읽기만) — 못 읽으면 None(이름만 로그)."""
    try:
        st = llmops_state()
    except Exception as e:   # noqa: BLE001 - LLMOps가 꺼져 있어도 감시는 지금처럼
        say("   결정자 읽기 실패", type(e).__name__)
        return None
    for p in st.get("projects") or []:
        for r in p.get("releases") or []:
            ro = (r.get("version") or {}).get("rollout") or {}
            if ro.get("change_id") == cid:
                return ro.get("decided_by")
    return None


CARRY = []   # 이번에 바꿔 끼운 버전 전환의 전 태그(D-588 C · 잠금 밖에서 kg_apply --carry-from)


def handle(req):
    d = json.loads(req.read_text())
    cid, version, rev = d["change_id"], d["version"], d["candidate_revision"]
    prev_tag = cfg_state((ROOT / CFG).read_text())[0] if KG else None   # ③ 설정 바꾸기 전 도는 태그(D-588 C)
    t0 = time.time()
    say(f"== {cid} {version} {rev[7:15]} 시작")
    if decided_by(cid) == AUTO_BY:
        if not AUTO_APPLY:
            failed(cid, version, "자동 반영 요청(auto-apply) — 이 감시는 자동 승인 꺼짐(AD_AUTO_APPLY=off) · 사람 승인 필요")
        say("   자동 반영 요청(auto-apply) — 자동 승인 켜짐(dev-2)")
    # ②
    rc, j = pack_tool(req, apply=False)
    if rc == 2:
        failed(cid, version, "Pack 받아 넣기: 요청 파일 꼴 틀림(format_error)")
    if rc != 0 or j.get("result") not in ("dry_run_ok", "already_applied_dry_run"):
        raise Stop(f"② dry-run 멈춤(exit {rc} · Pack 상태 파일 failed)")
    check_stop(cid, version)
    rc, j = pack_tool(req, apply=True)
    if rc == 2:
        failed(cid, version, "Pack 받아 넣기: 요청 파일 꼴 틀림(format_error)")
    ps = pack_status(cid)
    if rc != 0 or ps.get("stage") != "tagged" or ps.get("revision") != rev or len(ps.get("commit") or "") != 40:
        raise Stop(f"② apply 멈춤(exit {rc} · Pack 상태 {ps.get('stage')})")
    tag, commit = ps["tag"], ps["commit"]
    say(f"② tagged {tag} {commit[:12]} · {time.time() - t0:.0f}초")
    check_stop(cid, version)
    # 장부 줄
    t1, row = time.time(), None
    while time.time() - t1 < WAIT and not STOP:
        row = ledger_row(rev, tag)                   # D-645: revision만이 아니라 ② 태그까지 찬 줄
        if row:
            break
        time.sleep(POLL)
    row = row or ledger_row(rev)                     # 시간 다 됨 — 같은 revision 줄이 있으면 es3가 다른 점을 말하게
    if not row:
        check_stop(cid, version)
        failed(cid, version, f"배포단 등록: 장부 줄 없음({WAIT:.0f}초) — LLMOps 화면(그 Project)을 열어 두세요")
    say(f"   장부 줄 {row[0]} {row[1]} · {time.time() - t1:.0f}초 기다림")
    # ③
    try:
        say("   설정", set_config(tag, rev), "(커밋 0)")
    except ValueError as e:
        failed(cid, version, f"배포단 등록: {e}")
    es3 = ["scripts/py", "scripts/es3_check.py", "--config", CFG, "--id", cid, "--version", version, "--commit", commit, "--revision", rev, "--tag", tag, "--drs", row[1],
           "--status-dir", str(status_dir())]
    t2, told = time.time(), None
    while True:                                      # D-645: 상태 쓰기 없이 다시 물음(장부 줄이 다 찰 때까지 · ES3_WAIT) → 그 뒤 진짜 es3_check 한 번
        q = sh([*es3, "--no-status"], cwd=ROOT)
        why = next((l for l in reversed((q.stdout or "").strip().splitlines()) if l.strip()), "")
        if q.returncode == 0 or time.time() - t2 >= ES3_WAIT or STOP:
            break
        if why != told:
            say("   es3 대조 아직 ·", why[:160], "— 다시 물음")
            told = why
        time.sleep(POLL)
    say("   es3 대조", "통과" if q.returncode == 0 else "실패", "·", why[:160], f"· {time.time() - t2:.0f}초")
    r = sh(es3, cwd=ROOT)
    say("③ es3_check", r.returncode)
    say_stderr(r)
    if r.returncode:
        raise Stop("③ 등록 대조 실패(es3_check가 failed 씀)")
    check_stop(cid, version)
    # ④
    rid = switch(cid, version, tag, rev, row[1])
    if KG and prev_tag:
        CARRY.append(prev_tag)
    say(f"== {cid} 끝 switched {rid} · 합 {time.time() - t0:.0f}초")


# ── 되돌리기(D-633) ──

def rb_fields(d):
    """요청 파일 → (id, 배포 id, version, revision, tag 또는 None). 결정 evidence 칸 이름(target_deployment_id)도 받음."""
    return (d.get("rollback_id"), d.get("deployment_id") or d.get("target_deployment_id"), d.get("version"), d.get("revision"),
            d.get("tag") or None)


def rb_write(rid, stage, detail, **kw):
    """상태 파일 status/deployments/<id>.json — 9칸 · 같은 폴더 임시 파일 → os.replace(LLMOps가 반쯤 쓴 파일을 읽지 않게)."""
    rec = {"id": rid, "stage": stage, "detail": detail[:200], **kw,
           "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    rec = {k: rec.get(k) for k in STATUS_FIELDS}
    d = status_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{rid}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, d / f"{rid}.json")
    say(f"   상태 {rid} {stage} · {detail[:80]}")
    return rec


def rb_tag(rev):
    """장부 줄에서 그 revision의 태그 · drs — 같은 revision 줄이 여럿이면 마지막(가장 새) 줄. 없으면 None."""
    rows = [(t, drs) for t, r, drs in ledger() if r == rev]
    return rows[-1] if rows else None


def tag_rev(tag):
    import graph_swap   # 시험에서 바꿔 끼움 · kg_apply와 같은 함수(태그를 풀어 packrt로 revision 셈)
    return graph_swap.rev(tag)


def rb_swap(tag):
    import graph_swap
    return graph_swap.swap(tag)


def handle_rb(f):
    d = json.loads(f.read_text())
    rid, dep, version, rev, want_tag = rb_fields(d)
    base = {"version": version, "revision": rev, "deployment_id": dep}
    t0 = time.time()
    say(f"== {rid} 되돌리기 {version} {(rev or '')[7:15]} 시작")
    found = rb_tag(rev)
    if not found:
        rb_write(rid, "failed", f"배포단 등록 실패 — 장부 줄 없음: {dep} · revision {(rev or '')[7:15]}", **base)
        raise Stop("장부 줄 없음")
    tag, drs = found
    try:   # 대조는 revision(D-633 llmops-2): 같은 revision이 이미 떠 있으면 태그 이름이 달라도(장부 d619-g2 ↔ 8796 d622-g2) 다시 띄움 0
        i = get("/info") if listener() else {}
    except Exception:   # noqa: BLE001 - 못 읽으면 아래 전환 길
        i = {}
    p = i.get("pack") or {}
    if p.get("pack_revision") == rev and i.get("data_release_id") == drs:
        rb_write(rid, "registered", f"배포단 등록 {dep} · revision {rev[7:15]} · drs {drs.removeprefix('drs:')[:8]} 장부 줄 대조",
                 commit=p.get("commit"), tag=p.get("tag"), **base)
        rb_write(rid, "switched", f"{PORT} /info {p.get('tag')} {rev[7:15]} · 이미 같은 revision이 떠 있음 — 다시 띄움 0 · 시연 감시 되돌리기",
                 commit=p.get("commit"), tag=p.get("tag"), **base)
        say(f"== {rid} 끝 switched(이미 같은 revision · {p.get('tag')}) · 합 {time.time() - t0:.0f}초")
        return
    tag = want_tag or tag
    try:
        commit, trev, layer = tag_rev(tag)
    except Exception as e:   # noqa: BLE001 - 태그 없음 · Pack 읽기 실패: 이름만
        rb_write(rid, "failed", f"배포단 등록 실패 — 태그 {tag}를 읽지 못함({type(e).__name__})", **base)
        raise Stop("태그 읽기 실패")
    if trev != rev:
        rb_write(rid, "failed", f"배포단 등록 실패 — 태그 {tag} revision {(trev or '')[7:15]}이 요청 {(rev or '')[7:15]}와 다름", **base)
        raise Stop("태그 revision 다름")
    rb_write(rid, "registered", f"배포단 등록 {dep} · 태그 {tag} · revision {rev[7:15]} · drs {drs.removeprefix('drs:')[:8]} 장부 줄 대조",
             commit=commit, tag=tag, **base)
    check_rb_stop(rid, base, commit, tag)
    try:
        with swap_lock():
            rb_swap(tag)
    except Exception as e:   # noqa: BLE001 - SwapError 글은 이 저장소가 쓴 한 줄(값 0) · 그 밖은 이름만
        why = str(e)[:120] if type(e).__name__ == "SwapError" else type(e).__name__
        rb_write(rid, "failed", f"운영 전환 실패 — {why}", commit=commit, tag=tag, **base)
        raise Stop("전환 실패")
    try:
        i = get("/info")
    except Exception as e:   # noqa: BLE001
        rb_write(rid, "failed", f"운영 전환 실패 — /info 읽기 실패({type(e).__name__})", commit=commit, tag=tag, **base)
        raise Stop("/info 실패")
    p = i.get("pack") or {}
    if p.get("pack_revision") != rev or i.get("data_release_id") != drs:
        rb_write(rid, "failed", f"운영 전환 실패 — /info revision · drs가 기대와 다름({(p.get('pack_revision') or '')[7:15]})",
                 commit=commit, tag=tag, **base)
        raise Stop("/info 다름")
    rb_write(rid, "switched", f"{PORT} /info {p.get('tag')} {rev[7:15]} · layer {layer} · 시연 감시 되돌리기 {time.time() - t0:.0f}초",
             commit=p.get("commit") or commit, tag=p.get("tag") or tag, **base)
    say(f"== {rid} 끝 switched {tag} · 합 {time.time() - t0:.0f}초")


def check_rb_stop(rid, base, commit, tag):
    if STOP:
        rb_write(rid, "failed", "운영 전환 실패 — 감시 꺼짐(사람이 이어서)", commit=commit, tag=tag, **base)
        raise Stop("감시 꺼짐")


def rb_pick(baseline):
    """켠 뒤 새로 생긴(또는 다시 써진) 되돌리기 요청 중 처리 표시 · 상태 파일 없고 이 감시 배포 id인 것."""
    if not ROLLBACK:
        return []
    out = []
    dep = None
    for f in sorted(REQ.glob(f"{RB_PREFIX}*.json"), key=lambda p: p.stat().st_mtime):
        if baseline.get(f.name) == f.stat().st_mtime_ns or (STATE / "done" / f.stem).exists():
            continue
        if (status_dir() / f.name).exists():
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rid, fdep, version, rev, _ = rb_fields(d)
        if d.get("format") != RB_FORMAT or rid != f.stem or d.get("dry_run") is True or not (version and rev and fdep):
            continue
        dep = dep or load_settings(ROOT / CFG).deployment_id
        if fdep != dep:
            if f.stem not in TOLD:
                TOLD.add(f.stem)
                say(f"{f.stem} 다른 배포 id({fdep}) — 손 0")
            continue
        out.append(f)
    return out


# ── 감시 ──

def pick(baseline):
    out = []
    for f in sorted(REQ.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if baseline.get(f.name) == f.stat().st_mtime_ns:   # 켤 때 있던 그대로면 손 0(다시 써진 같은 이름은 새 승인)
            continue
        mark = STATE / "done" / f.stem
        if mark.exists():
            if f.stat().st_mtime > mark.stat().st_mtime and f.stem not in TOLD:
                TOLD.add(f.stem)
                say(f"{f.stem} 다시 씀 · 이미 처리한 change id — 손 0")
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue   # 쓰는 중일 수 있음 — 다음 바퀴
        if d.get("format") != FORMAT or not str(d.get("change_id", "")).startswith(PREFIX) or d.get("dry_run") is True \
                or d.get("change_id") != f.stem:
            continue
        if (status_dir() / f.name).exists():
            continue
        out.append(f)
    return out


def claim(cid):
    try:
        os.close(os.open(STATE / "done" / cid, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
        return True
    except FileExistsError:
        return False


def need_tool():
    if TOOL is None or not TOOL.is_file():
        raise SystemExit(f"멈춤: c1a15-pack 받아 넣기 도구가 없음({TOOL}) — 그 가지 작업 트리이거나 AD_PACK_TOOL로 경로를 주세요 · 켜지 않음")


@contextmanager
def swap_lock():
    """버전 전환을 kg_apply와 같은 잠금 안에서(AD_KG=on일 때만) — kg_apply는 스스로 이 잠금을 잡으므로 kg_apply를 부를 때는 잡지 않음."""
    if not KG:
        yield
        return
    KG_STATE.mkdir(parents=True, exist_ok=True)
    with open(KG_STATE / "kg-apply.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def kg_pending():
    """kg_apply가 처리할 것 — KG 반영 대기 폴더(로그)의 처리 표시(done) 없는 승인 기록 파일(KG_LETTERS가 off가 아닐 때)
    + 후보 그래프 폴더의 처리 · 꼴 틀림 표시 없는 파일. 앱 · LLMOps는 임시 이름(.…tmp)으로 쓴 뒤 이름을 바꿈."""
    if not KG:
        return []
    out = []
    if KG_LETTERS and KG_INBOX and KG_INBOX.is_dir():
        out += [f.stem for f in sorted(KG_INBOX.glob("*.json")) if not f.name.startswith(".") and not (KG_STATE / "done" / f.stem).exists()]
    if KG_CANDS and KG_CANDS.is_dir():
        out += [f.stem for f in sorted(KG_CANDS.glob("*.json")) if not f.name.startswith(".")
                and not (KG_STATE / "done-candidates" / f.stem).exists() and not (KG_STATE / "failed-candidates" / f.stem).exists()]
    return out


def kg_carry(prev):
    say(f"버전 전환 뒤 배운 예외 그래프 옮겨 싣기(전 태그 {prev}) → kg_apply")
    r = sh(["scripts/py", "scripts/kg_apply.py", "--carry-from", prev], cwd=ROOT)
    say(f"   kg_apply --carry-from 끝 {r.returncode}")
    say_stderr(r)
    return r.returncode


def kg_apply_once(names):
    say(f"KG 새 것 {names} → kg_apply")
    r = sh(["scripts/py", "scripts/kg_apply.py"], cwd=ROOT)
    say(f"   kg_apply 끝 {r.returncode}" + (" — 같은 묶음으로는 다시 부르지 않음(작업 폴더 kg-apply.log)" if r.returncode else ""))
    say_stderr(r)
    return r.returncode


def run(once=False, baseline=None):   # baseline: 시험만(기본 = 켤 때 있던 파일 이름 → mtime)
    if VERSIONS:
        need_tool()
    if KG and (KG_STATE is None or (KG_CANDS is None and not (KG_LETTERS and KG_INBOX))):
        raise SystemExit("멈춤: AD_KG=on인데 KG_STATE 또는 볼 폴더(KG_INBOX · KG_CANDIDATES)가 없음 · 켜지 않음")
    signal.signal(signal.SIGTERM, on_term)
    (STATE / "done").mkdir(parents=True, exist_ok=True)
    rb_base = {f.name: f.stat().st_mtime_ns for f in REQ.glob(f"{RB_PREFIX}*.json")} if baseline is None else baseline   # 켤 때 있던 되돌리기 요청 손 0
    if not VERSIONS:
        baseline = {}
    baseline = {f.name: f.stat().st_mtime_ns for f in REQ.glob("*.json")} if baseline is None else baseline
    (STATE / "baseline.txt").write_text("\n".join(sorted(baseline)) + "\n")
    dep = load_settings(ROOT / CFG).deployment_id
    say(f"감시 시작 pid {os.getpid()} · 요청 폴더 {REQ if VERSIONS else '안 봄(AD_VERSIONS=off)'} · 기준선 {len(baseline)} · 배포 {dep} · 포트 {PORT}"
        + (f" · --by {'있음' if has_by() else '없음'}" if VERSIONS else "") + (f" · 되돌리기 요청 {REQ} · 기준선 {len(rb_base)}" if ROLLBACK else "") + (f" · KG 로그 {KG_INBOX if KG_LETTERS else '안 봄'} · 후보 그래프 {KG_CANDS}" if KG else ""))
    kg_failed = None
    while not STOP:
        for f in (pick(baseline) if VERSIONS else []):
            if STOP:
                break
            if not claim(f.stem):
                say(f"{f.stem} 이미 처리 — 손 0")
                continue
            try:
                with swap_lock():
                    handle(f)
                while CARRY:                          # 잠금 밖(kg_apply가 같은 잠금을 잡음)
                    kg_carry(CARRY.pop(0))
            except Stop as e:
                say(f"== {f.stem} 멈춤: {e}")
            except (Exception, SystemExit) as e:   # 예상 밖(listener 멈춤 SystemExit 포함) — 이 건만 멈춤, 글은 이름만
                say(f"== {f.stem} 예상 밖 멈춤 {type(e).__name__}")
                try:
                    d = json.loads(f.read_text())
                    deploy_status("failed", d["change_id"], d["version"], detail=f"자동 감시 예상 밖 멈춤({type(e).__name__}) — 사람이 이어서")
                except Exception:
                    pass
        for f in rb_pick(rb_base):
            if STOP:
                break
            if not claim(f.stem):
                continue
            try:
                handle_rb(f)
            except Stop as e:
                say(f"== {f.stem} 멈춤: {e}")
            except (Exception, SystemExit) as e:   # 예상 밖 — 이 건만 멈춤, 글은 이름만
                say(f"== {f.stem} 예상 밖 멈춤 {type(e).__name__}")
                try:
                    rid, dep, version, rev, _ = rb_fields(json.loads(f.read_text()))
                    rb_write(rid, "failed", f"운영 전환 실패 — 자동 감시 예상 밖 멈춤({type(e).__name__}) · 사람이 이어서",
                             version=version, revision=rev, deployment_id=dep)
                except Exception:
                    pass
        pend = kg_pending()
        if pend and pend != kg_failed and not STOP:
            kg_failed = pend if kg_apply_once(pend) else None
        if once:
            break
        time.sleep(POLL)
    say("감시 끝")


def pidfile():
    return STATE / "pid"


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def start():
    if VERSIONS:
        need_tool()
    STATE.mkdir(parents=True, exist_ok=True)
    pf = pidfile()
    if pf.exists() and alive(int(pf.read_text() or 0)):
        raise SystemExit(f"이미 켜짐 pid {pf.read_text().strip()}")
    log = ROOT / "var" / "logs" / "autodeploy.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run"], cwd=ROOT, env=os.environ.copy(),
                         stdout=open(log, "a"), stderr=subprocess.STDOUT, start_new_session=True)
    pf.write_text(str(p.pid))
    time.sleep(1.5)
    say("켬 pid", p.pid, "· 로그", log, "·", (log.read_text().strip().splitlines() or [""])[-1][9:160])


def stop():
    pf = pidfile()
    pid = int(pf.read_text() or 0) if pf.exists() else 0
    if not pid or not alive(pid):
        say("꺼져 있음")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(600):
        if not alive(pid):
            break
        time.sleep(0.5)
    say("끔 pid", pid, "· 살아 있음" if alive(pid) else "")
    pf.unlink(missing_ok=True)


def status():
    pf = pidfile()
    pid = int(pf.read_text() or 0) if pf.exists() else 0
    done = sorted(p.name for p in (STATE / "done").glob("*")) if (STATE / "done").exists() else []
    log = ROOT / "var" / "logs" / "autodeploy.log"
    last = (log.read_text().strip().splitlines() or [""])[-1] if log.exists() else ""
    say("켜짐" if pid and alive(pid) else "꺼짐", pid or "", "· 처리", done, "· 마지막", last[:160])


if __name__ == "__main__":
    cmds = {"run": run, "once": lambda: run(once=True), "start": start, "stop": stop, "status": status}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        raise SystemExit("사용: scripts/py scripts/autodeploy.py start|stop|status|run|once")
    cmds[sys.argv[1]]()
