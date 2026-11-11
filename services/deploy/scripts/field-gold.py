"""체크 기록 → 현장 정답 파일 놓기 일꾼(D-470 덧 5 · 6, VFE EV-005 FIELD-GOLD-E1) — 서버가 쓴 일감(<data_dir>/field-gold/outbox)을 2초마다 봄.
한 건: CLI · make_gold · 이력 corpus sha256 대조(다르거나 없으면 돌리지 않음) → 그 서버가 쓴 release 폴더 둘(history · manuals)
  → `<이 Python> -I field_gold.py` 를 작업 폴더에 돌림 → 놓기 규칙(오케 D-470 덧 6 · 리드 Q2):
  exit 0 · schema c1a15.field-gold.v1 · record_key = 일감 · gate.ok true · expected 있음 · 이름 [A-Za-z0-9._-] 1 ~ 128자
    → <out_dir>/<record_key>.json(원자) — 이미 있으면 같으면 그대로 · 다르면 덮어쓰지 않고 <record_key>.conflict.json만
  그 밖(문 막힘 · exit 2 · 예외 · sha 다름 · release 모름) → 놓지 않음 · 상태 파일에 까닭.
자동 다시 돌림 0(사람 손 retry). 상태 <data_dir>/field-gold/state/<record_key>.json · 일감은 처리 뒤 outbox/done/. 망 · DB · 실제 호출 0.
사용(배포단 루트): scripts/py scripts/field-gold.py start|stop|status|run|once|retry <record_key>   값(env): FG_CONFIG(config/c1a15-dev-local.yaml) · FG_POLL(2)"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy_server.settings import load_settings  # noqa: E402

CFG = os.environ.get("FG_CONFIG") or "config/c1a15-dev-local.yaml"
POLL = float(os.environ.get("FG_POLL") or 2)
SCHEMA = "c1a15.field-gold.v1"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")   # 리드 Q2: LLMOps가 파일 이름으로 읽는 기록 번호 규칙
HISTORY, MANUALS = "history", "manuals"            # C1-a15 Pack store 이름
STOP = False
P = None   # paths(settings) — run/once 때 채움


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def on_term(*_):
    global STOP
    STOP = True


def paths(s):
    fg = s.field_gold
    if not fg:
        raise SystemExit(f"멈춤: 설정 {CFG}에 field_gold 칸이 없음 — 켜지 않음")
    base = s.data_dir / "field-gold"
    return {"out": fg["out_dir"], "cli": fg["cli"], "corpus": fg["history_corpus"], "sha": fg["sha256"],
            "outbox": base / "outbox", "state": base / "state", "work": base / "work",
            "pid": ROOT / "var" / "field-gold" / "pid", "log": ROOT / "var" / "logs" / "field-gold.log"}


def name_ok(rk):
    return isinstance(rk, str) and bool(NAME_RE.match(rk)) and rk not in (".", "..")


def file_sha(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def canon_sha(doc):
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def set_state(rk, stage, **kw):
    write_atomic(P["state"] / f"{rk}.json", json.dumps({"record_key": rk, "stage": stage, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **kw},
                                                       ensure_ascii=False))
    say(f"   {rk} → {stage}", " ".join(f"{k}={v}" for k, v in kw.items() if k != "new")[:160])
    return stage


def sha_check():
    """다른 파일 이름 목록(없음도 다름)."""
    want = P["sha"]
    have = {"field_gold": file_sha(P["cli"]), "make_gold": file_sha(Path(P["cli"]).parent / "make_gold.py"), "history_corpus": file_sha(P["corpus"])}
    return sorted(k for k in want if have.get(k) != want[k])


def judge(rk, job):
    """한 건 → 상태 이름. 놓기 · 충돌 · 거절은 여기서."""
    if not name_ok(rk):
        return set_state(rk if name_ok(rk) else "_bad-name", "bad_record_key", record_key_seen=str(rk)[:140])
    bad = sha_check()
    if bad:
        return set_state(rk, "tool_sha_mismatch", which=bad)
    st = job.get("stores") or {}
    hist, man = (st.get(HISTORY) or {}).get("dir"), (st.get(MANUALS) or {}).get("dir")
    if not job.get("release_match") or not hist or not man:
        return set_state(rk, "release_unknown", release_match=job.get("release_match"), history=bool(hist), manuals=bool(man))
    P["work"].mkdir(parents=True, exist_ok=True)
    rec_file, out_file = P["work"] / f"{rk}.record.json", P["work"] / f"{rk}.json"
    rec_file.write_text(json.dumps(job["record"], ensure_ascii=False), encoding="utf-8")
    out_file.unlink(missing_ok=True)
    r = subprocess.run([sys.executable, "-I", str(P["cli"]), "--record", str(rec_file), "--history-release", hist, "--manual-release", man,
                        "--history-corpus", str(P["corpus"]), "--out", str(out_file)], capture_output=True, text=True, timeout=300)
    if r.returncode == 2:
        return set_state(rk, "input_error", exit=2, stderr=(r.stderr.strip().splitlines() or [""])[0][:300])
    if r.returncode != 0 or not out_file.exists():
        return set_state(rk, "crash", exit=r.returncode)
    try:
        new = json.loads(out_file.read_text(encoding="utf-8"))
    except ValueError:
        return set_state(rk, "crash", exit=0, why="출력 JSON 아님")
    gate = new.get("gate") or {}
    if new.get("schema") != SCHEMA or new.get("record_key") != rk:
        return set_state(rk, "crash", exit=0, why="출력 schema · record_key 다름")
    if gate.get("ok") is not True or not isinstance(new.get("expected"), dict):
        return set_state(rk, "gate_blocked", failed=gate.get("failed") or [])
    dest = P["out"] / f"{rk}.json"
    if dest.exists():
        try:
            kept = canon_sha(json.loads(dest.read_text(encoding="utf-8")))
        except ValueError:
            kept = file_sha(dest)
        if kept == canon_sha(new):
            return set_state(rk, "same", sha256=kept)
        write_atomic(P["out"] / f"{rk}.conflict.json", json.dumps({"record_key": rk, "kept_sha256": kept, "new_sha256": canon_sha(new),
                                                                   "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "new": new}, ensure_ascii=False))
        return set_state(rk, "conflict", kept_sha256=kept, new_sha256=canon_sha(new))
    write_atomic(dest, out_file.read_text(encoding="utf-8"))
    return set_state(rk, "placed", sha256=canon_sha(new))


def process(job_path):
    job_path = Path(job_path)
    rk = job_path.stem
    say(f"== {rk} 시작")
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        stage = judge(rk, job)
    except Exception as e:  # noqa: BLE001 - 한 건만 멈춤, 글은 형 이름만
        stage = set_state(rk if name_ok(rk) else "_bad-name", "crash", why=type(e).__name__)
    done = P["outbox"] / "done"
    done.mkdir(parents=True, exist_ok=True)
    os.replace(job_path, done / job_path.name)
    say(f"== {rk} 끝 {stage}")
    return stage


def retry(rk):
    """사람 손: done의 일감을 outbox로 되돌리고 상태를 지움(놓인 파일은 손 0 — 바뀌면 충돌 규칙)."""
    src = P["outbox"] / "done" / f"{rk}.json"
    if not name_ok(rk) or not src.exists():
        raise SystemExit(f"멈춤: 처리한 일감 {rk}가 없음")
    os.replace(src, P["outbox"] / f"{rk}.json")
    (P["state"] / f"{rk}.json").unlink(missing_ok=True)
    say(f"{rk} 다시 줄 세움")


def pending():
    return sorted(P["outbox"].glob("*.json"), key=lambda p: p.stat().st_mtime) if P["outbox"].exists() else []


def run(once=False):
    signal.signal(signal.SIGTERM, on_term)
    say(f"일꾼 시작 pid {os.getpid()} · 일감 {P['outbox']} · 놓는 곳 {P['out']} · 남은 일감 {len(pending())} · sha 다름 {sha_check() or 0}")
    while not STOP:
        for f in pending():
            if STOP:
                break
            process(f)
        if once:
            break
        time.sleep(POLL)
    say("일꾼 끝")


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def start():
    pf, log = P["pid"], P["log"]
    if pf.exists() and alive(int(pf.read_text() or 0)):
        raise SystemExit(f"이미 켜짐 pid {pf.read_text().strip()}")
    pf.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run"], cwd=ROOT, env=os.environ.copy(),
                         stdout=open(log, "a"), stderr=subprocess.STDOUT, start_new_session=True)
    pf.write_text(str(p.pid))
    time.sleep(1.5)
    say("켬 pid", p.pid, "· 로그", log, "·", (log.read_text().strip().splitlines() or [""])[-1][9:160])


def stop():
    pf = P["pid"]
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
    pf = P["pid"]
    pid = int(pf.read_text() or 0) if pf.exists() else 0
    states = {}
    for f in P["state"].glob("*.json") if P["state"].exists() else []:
        states.setdefault(json.loads(f.read_text()).get("stage"), []).append(f.stem)
    say("켜짐" if pid and alive(pid) else "꺼짐", pid or "", "· 남은 일감", len(pending()), "· 상태", states, "· sha 다름", sha_check() or 0)


if __name__ == "__main__":
    cmds = {"run": run, "once": lambda: run(once=True), "start": start, "stop": stop, "status": status}
    if not (len(sys.argv) == 2 and sys.argv[1] in cmds) and not (len(sys.argv) == 3 and sys.argv[1] == "retry"):
        raise SystemExit("사용: scripts/py scripts/field-gold.py start|stop|status|run|once|retry <record_key>")
    P = paths(load_settings(ROOT / CFG))
    retry(sys.argv[2]) if sys.argv[1] == "retry" else cmds[sys.argv[1]]()
