"""F-10 빌드 commit 대조 ①(VFE EV-005 ES-G 준비, 보고 023 3절 · 지시 029): 이미지 빌드 안에서 pip가 git 태그로 설치한 packrt · tracewriter의
direct_url.json `vcs_info.commit_id`가 pinned.json `packrt_repo.commit`(40자)과 같은지 본다(설치 뒤 보조 확인). 설치 자체는 Dockerfile이
pinned.json commit으로 하므로(태그로 설치하지 않음 — 바꿔친 태그의 코드가 설치 중에 실행되지 않게) 이 확인은 설치 줄이 바뀌거나 빠진 경우를 잡는다.
②(deploy clone HEAD = 빌드 인자 DEPLOY_COMMIT)는 이 파일을 믿기 전에 Dockerfile 셸 한 줄이 먼저 본다.

표준 라이브러리만. 빌드의 venv python으로 부른다(그 venv에 설치된 배포판을 읽음):
  /opt/venv/bin/python scripts/build_commit_check.py packrt --pinned pinned.json --tag "$PACKRT_TAG"
종료: 0 같음 / 1 다름 · 칸 없음 / 2 사용 오류."""
import argparse
import json
import re
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlsplit

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
NAMES = ("packrt", "tracewriter")   # 같은 태그 · 같은 저장소의 하위 폴더(D-63 · D-69)


def repo_path(url):
    """저장소 주소 → 'host/owner/name'(https · ssh · git+ssh · scp 꼴 모두, 사용자 · 포트 · 끝 .git · / 뺌)."""
    u = (url or "").removeprefix("git+")
    if "://" in u:
        s = urlsplit(u)
        host, path = s.hostname or "", s.path
    else:
        left, _, path = u.partition(":")
        host = left.rpartition("@")[2]
    return f"{host.lower()}/{path.strip('/').removesuffix('.git').lower()}"


def check_one(name, direct_url_text, pin, tag):
    """(통과 여부, 한 줄). direct_url_text = 설치된 배포판의 direct_url.json 글(없으면 None)."""
    pr = pin.get("packrt_repo") or {}
    want = pr.get("commit") or ""
    if not COMMIT_RE.fullmatch(want):
        return False, f"{name}: 멈춤 — pinned.json packrt_repo.commit이 40자 commit이 아님({len(want)}자)"
    if direct_url_text is None:
        return False, f"{name}: 멈춤 — direct_url.json 없음(git 태그로 설치되지 않음)"
    try:
        d = json.loads(direct_url_text)
    except ValueError:
        return False, f"{name}: 멈춤 — direct_url.json이 JSON이 아님"
    vcs = d.get("vcs_info") if isinstance(d, dict) else None
    got = vcs.get("commit_id") if isinstance(vcs, dict) else None
    if not got:
        return False, f"{name}: 멈춤 — direct_url.json에 vcs_info.commit_id 칸 없음"
    if not isinstance(got, str):
        return False, f"{name}: 멈춤 — vcs_info.commit_id가 글자가 아님"
    if not COMMIT_RE.fullmatch(got):
        return False, f"{name}: 멈춤 — 설치 commit이 40자 commit이 아님({got[:12]}, {len(got)}자)"
    if got != want:
        return False, f"{name}: 멈춤 — 설치 commit {got[:12]} ≠ pinned.json {want[:12]}(태그 {tag}가 다른 commit을 가리킴)"
    if vcs.get("requested_revision") != want:   # Dockerfile은 태그가 아니라 pinned.json commit으로 설치함
        return False, f"{name}: 멈춤 — 설치에 쓴 판 {vcs.get('requested_revision')!r} ≠ pinned.json commit(태그 {tag} 대신 commit으로 설치해야 함)"
    if d.get("subdirectory") != name:
        return False, f"{name}: 멈춤 — 설치 하위 폴더 {d.get('subdirectory')!r} ≠ {name!r}"
    if pr.get("url") and repo_path(d.get("url")) != repo_path(pr["url"]):
        return False, f"{name}: 멈춤 — 설치 저장소 {repo_path(d.get('url'))} ≠ pinned.json {repo_path(pr['url'])}"
    return True, f"{name}: 같음 — {tag} → {got[:12]}(pinned.json과 같음)"


def read_direct_url(name):
    try:
        return distribution(name).read_text("direct_url.json")
    except PackageNotFoundError:
        return None


def check_packrt(pin, tag, read=None):
    """(통과 여부, 줄 목록). PACKRT_TAG = pinned.json packrt_repo.tag이고 packrt · tracewriter 둘 다 같은 commit이어야 통과."""
    lines, ok = [], True
    want_tag = (pin.get("packrt_repo") or {}).get("tag")
    if not tag or tag != want_tag:
        return False, [f"멈춤 — 빌드 인자 PACKRT_TAG {tag!r} ≠ pinned.json packrt_repo.tag {want_tag!r}"]
    read = read or read_direct_url
    for name in NAMES:
        good, line = check_one(name, read(name), pin, tag)
        ok &= good
        lines.append(line)
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="F-10 ① packrt · tracewriter 설치 commit = pinned.json")
    ap.add_argument("what", choices=["packrt"])
    ap.add_argument("--pinned", required=True)
    ap.add_argument("--tag", required=True)
    a = ap.parse_args(argv)
    try:
        pin = json.loads(Path(a.pinned).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"사용 오류: pinned.json을 읽을 수 없음({type(e).__name__})", file=sys.stderr)
        return 2
    ok, lines = check_packrt(pin, a.tag)
    print("F-10 ① " + ("같음" if ok else "다름 — 빌드를 멈춥니다"))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
