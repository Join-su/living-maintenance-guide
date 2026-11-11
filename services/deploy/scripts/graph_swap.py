#!/usr/bin/env python3
"""그래프 버전 바꿔 끼우기(D-545 · D-546 K6 · dev-2) — 그래프 새 버전 = Pack revision 하나(pack/templates/graph/current.json).
배포 서버 코드는 그대로이고, 설정(AD_CONFIG)의 ref · expect_revision 두 칸만 바꿉니다(autodeploy.set_config).
그다음 r12-update check → 그 포트의 이 저장소 서버만 다시 띄움 → /info revision 대조. 어디서 실패해도 설정은 옛 값으로 돌아가고,
서버를 내린 뒤였다면 옛 설정으로 다시 띄웁니다.

사용(cwd = 이 저장소, scripts/py로 · 환경 AD_CONFIG · AD_PORT · AD_SERVE_LOG는 autodeploy와 같은 이름):
  scripts/py scripts/graph_swap.py rev  <tag>          태그의 Pack revision · 그래프 layer(바꾸는 것 0)
  scripts/py scripts/graph_swap.py swap <tag>          바꿔 끼우기(되돌림도 같은 명령)
  scripts/py scripts/graph_swap.py run  [요청 파일]     start 요청 한 번 → 201 · exception_guide.relation_id(기본: Pack 약속 최신 start 예시)
DSN · 비밀 값 · provider 오류 글은 출력하지 않습니다."""
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autodeploy as A  # noqa: E402  설정 칸 바꾸기 · 포트 주인 확인 · /info(감시와 같은 함수)
from deploy_server.settings import load_settings  # noqa: E402

GRAPH = "templates/graph/current.json"


class SwapError(RuntimeError):
    pass


def say(*a):
    A.say(*a)


def pack_repo():
    return Path(load_settings(A.ROOT / A.CFG).pack_repo)


def rev(tag, repo=None):
    """태그를 임시 폴더에 풀어 packrt로 revision을 셈 → (commit, revision, layer 또는 None)."""
    repo = Path(repo or pack_repo())
    c = A.sh(["git", "-C", str(repo), "rev-parse", "-q", "--verify", f"{tag}^{{commit}}"]).stdout.strip()
    if not c:
        raise SwapError(f"Pack 저장소에 태그 {tag}가 없음")
    data = subprocess.run(["git", "-C", str(repo), "archive", tag, "pack"], capture_output=True, check=True).stdout
    from packrt import ir
    with tempfile.TemporaryDirectory() as d:
        tarfile.open(fileobj=io.BytesIO(data)).extractall(d, filter="data")
        r = ir.load_pack(Path(d) / "pack")
        if not r.ok:
            raise SwapError(f"{tag} Pack 읽기 실패 — 오류 {len(r.errors)}건")
        g = Path(d) / "pack" / GRAPH
        layer = json.loads(g.read_text(encoding="utf-8")).get("layer") if g.exists() else None
    return c, r.revision, layer


def _serve():
    subprocess.Popen(["nohup", "scripts/serve.sh"], cwd=A.ROOT, env={**os.environ, "DEPLOY_CONFIG": str(A.CFG), "DEPLOY_PORT": str(A.PORT)},
                     stdout=open(A.LOG, "a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(150):
        try:
            A.get("/health", 1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _term(pid):
    subprocess.run(["kill", "-TERM", pid])
    for _ in range(50):
        if not A.sh(["lsof", "-nP", "-t", f"-iTCP:{A.PORT}", "-sTCP:LISTEN"]).stdout.strip():
            return
        time.sleep(0.2)
    raise SwapError(f"{A.PORT}이 10초 안에 비지 않음")


def swap(tag):
    """설정 ref/expect → tag/그 revision · check · 다시 띄움 · /info 대조. 실패하면 설정 · 서버를 옛 것으로."""
    _, revision, layer = rev(tag)
    p = A.ROOT / A.CFG
    old = p.read_text()
    changed = A.set_config(tag, revision)
    pid = A.listener()
    if changed == "같음" and pid and A.info_line()[1] == revision:
        say(f"이미 {tag} — 다시 띄움 0")
        return revision
    r = A.sh(["scripts/py", "scripts/r12-update", "check", "--config", str(A.CFG)], cwd=A.ROOT)
    say("r12-update check", r.returncode)
    if r.returncode:
        p.write_text(old)
        raise SwapError("묶기 대조 실패 — 설정 옛 값 · 서버 손 0")
    t0 = time.time()
    if pid:
        _term(pid)
    if not _serve():
        p.write_text(old)
        back = _serve()
        raise SwapError(f"서버가 30초 안에 뜨지 않음 — 설정 옛 값 · 옛 서버 {'다시 뜸' if back else '안 뜸'} · 로그 {A.LOG}")
    now = A.info_line()[1]
    if now != revision:
        raise SwapError(f"/info revision {(now or '')[7:15]}이 기대 {revision[7:15]}와 다름")
    say(f"바꿔 띄움 {changed} · {time.time() - t0:.1f}초 · pid {A.listener()} · revision {revision[7:15]} · layer {layer}")
    return revision


def run_start(req=None):
    """start 요청 한 번 → (run_id, exception_guide.relation_id 또는 None). 201 아니면 code · where · HTTP만."""
    if req is None:
        vs = sorted((pack_repo() / "contracts").glob("v*/examples/start.request.json"), key=lambda q: int(q.parent.parent.name[1:]))
        req = vs[-1]
    rq = urllib.request.Request(A.URL + "/runs", data=Path(req).read_bytes(), method="POST",
                                headers={"Content-Type": "application/json", "Idempotency-Key": f"gs-{time.time_ns()}"})
    try:
        with urllib.request.urlopen(rq, timeout=180) as resp:
            d = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error") or {}
        except Exception:
            err = {}
        raise SwapError(f"start HTTP {e.code} · code {err.get('code')} · where {err.get('where')}") from None
    eg = ((d.get("result") or {}).get("output") or {}).get("exception_guide") or {}
    say(f"start 201 · {d.get('status')} · run {d.get('run_id')} · exception_guide {eg.get('relation_id') or '없음'}")
    return d.get("run_id"), eg.get("relation_id")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["?"]
    try:
        if cmd == "rev":
            c, r, layer = rev(args[0])
            say(f"{args[0]} commit {c[:7]} · revision {r[7:15]} · layer {layer}")
        elif cmd == "swap":
            swap(args[0])
        elif cmd == "run":
            run_start(*args)
        else:
            raise SystemExit(__doc__)
    except SwapError as e:
        raise SystemExit(f"멈춤: {e}")
