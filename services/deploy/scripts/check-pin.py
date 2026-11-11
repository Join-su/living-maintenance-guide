"""V-14(전용 환경): 설치본이 pinned.json의 packrt 저장소 태그와 같은지 본다(VFE EV-005 ES-D CHG-01, V-35 · V-38).
- 태그가 지금도 pinned.json의 commit을 가리킴(다시 찍힌 태그 거부), packrt · tracewriter tree가 기록과 같음
- packrt · tracewriter: 판 · 비편집 · .venv 안 · 파일 바이트 = 그 commit의 archive
- packrt가 고정한 판(langgraph · langsmith) = 설치 판, 제3자 판의 llmops uv.lock sha256 = 기록
- 설치된 패키지 전부(이름 · 판) = requirements/deploy-venv.txt (+ 맥 덧목록 requirements/onnx-extra.txt가 있으면 함께 — 갈래 k KS-3, D-265 Q-k2 (가))
출력은 일치/불일치와 다른 파일 이름뿐. 사용: scripts/py scripts/check-pin.py"""
import hashlib, io, json, os, subprocess, sys, tarfile
from importlib.metadata import PackageNotFoundError, distribution, distributions, version
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def tag_ref(repo, tag):
    """(태그 객체, commit), 태그가 없으면 (None, None)."""
    try:
        return (git(repo, "rev-parse", "--verify", f"refs/tags/{tag}").decode().strip(),
                git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").decode().strip())
    except subprocess.CalledProcessError:
        return None, None


def norm(name):
    return name.lower().replace("_", "-").replace(".", "-")


EXTRA = "onnx-extra.txt"   # 맥 전용 덧목록(배포 이미지 목록 · VPS 빌드에는 넣지 않음)


def wanted_versions(req, extra):
    """기대 판 = 기본 목록 + 덧목록(있으면). 같은 이름이 두 곳에 있으면 판이 같아야 함(다르면 그 이름은 '?'로 두어 불일치)."""
    want = req_versions(req)
    if extra is not None and Path(extra).is_file():
        for n, v in req_versions(extra).items():
            want[n] = v if want.get(n, v) == v else "?"
    return want


def req_versions(path):
    """requirements 파일 → {이름: 판}(주석 · 빈 줄 제외)."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, v = line.split("==", 1)
            out[norm(name)] = v.split(";")[0].strip().rstrip("\\").strip()   # hash 꼴 목록의 줄 끝 이어짐 표시(\) 뗌
    return out


def installed_versions():
    return {norm(d.metadata["Name"]): d.version for d in distributions()}


def inside(path, venv):
    return Path(path).resolve().is_relative_to(Path(venv).resolve())


def archive_files(repo, commit, name):
    """그 commit의 <name>/<name>/ 파일 → {"<name>/…": sha256}."""
    want = {}
    with tarfile.open(fileobj=io.BytesIO(git(repo, "archive", commit, f"{name}/{name}"))) as tf:
        for m in tf.getmembers():
            if m.isfile() and "__pycache__" not in m.name:
                want[m.name[len(name) + 1:]] = hashlib.sha256(tf.extractfile(m).read()).hexdigest()
    return want


def check(pin, venv, packrt_repo, llmops_repo, dist=distribution, ver=version, installed=installed_versions, req=None):
    """(일치 여부, 줄 목록). 없는 패키지 · 저장소 · commit도 traceback 대신 불일치 줄로 낸다."""
    lines, ok = [], True
    pr = pin["packrt_repo"]
    obj, now = tag_ref(packrt_repo, pr["tag"])
    tag_ok = now == pr["commit"] and obj == pr["tag_object"]
    def tree(n):
        try:
            return git(packrt_repo, "rev-parse", f"{pr['commit']}:{n}").decode().strip() if tag_ok else None
        except subprocess.CalledProcessError:
            return None
    trees = {n: tree(n) for n in ("packrt", "tracewriter")}
    tree_ok = tag_ok and all(trees[n] == pin[n]["tree"] for n in trees)
    ok &= tag_ok and tree_ok
    lines.append(f"태그: {'일치' if tag_ok and tree_ok else '불일치'} — {pr['name']} {pr['tag']} → "
                 f"{(now or '없음')[:12]}(기록 {pr['commit'][:12]}), tree {'같음' if tree_ok else '다름'}")
    for name in ("packrt", "tracewriter"):
        try:
            d = dist(name)
        except PackageNotFoundError:
            ok = False
            lines.append(f"{name}: 불일치 — 설치되지 않음")
            continue
        direct = d.read_text("direct_url.json")
        editable = bool(direct and json.loads(direct).get("dir_info", {}).get("editable"))
        ver_ok = d.version == pin[name]["version"]
        try:
            want = archive_files(packrt_repo, pr["commit"], name) if tag_ok else {}
        except subprocess.CalledProcessError:
            want = {}
        pkg_dir = Path(d.locate_file(name))
        have = {f"{name}/{p.relative_to(pkg_dir).as_posix()}": hashlib.sha256(p.read_bytes()).hexdigest()
                for p in pkg_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        diff = sorted(k for k in set(want) | set(have) if want.get(k) != have.get(k))
        here = inside(pkg_dir, venv)
        good = tag_ok and ver_ok and not editable and not diff and here
        ok &= good
        lines.append(f"{name}: {'일치' if good else '불일치'} — 판 {d.version}({'같음' if ver_ok else '다름'}), editable {editable}, "
                     f"위치 .venv 안 {here}, 파일 {len(have)}개 다른 파일 {diff or '없음'}")
    for name, want_v in sorted(pin["fixed_by_packrt"].items()):
        try:
            have_v = ver(name)
        except PackageNotFoundError:
            have_v = None
        ok &= have_v == want_v
        lines.append(f"{name}: {'일치' if have_v == want_v else '불일치'} — 설치 {have_v} / packrt 고정 {want_v}")
    tp = pin["third_party"]
    try:
        lock_ok = hashlib.sha256(git(llmops_repo, "show", f"{tp['llmops_commit']}:uv.lock")).hexdigest() == tp["uv_lock_sha256"]
    except subprocess.CalledProcessError:
        lock_ok = False
    ok &= lock_ok
    lines.append(f"uv.lock: {'일치' if lock_ok else '불일치'} — 제3자 판 llmops {tp['llmops_commit'][:12]} uv.lock sha256 = 기록")
    want = wanted_versions(req or HERE / "requirements" / "deploy-venv.txt", None if req else HERE / "requirements" / EXTRA)
    have = installed()
    differ = sorted(n for n in set(want) | set(have) if want.get(n) != have.get(n))
    ok &= not differ
    lines.append(f"설치 판: {'일치' if not differ else '불일치'} — 패키지 {len(have)}개 = requirements/deploy-venv.txt"
                 f"{' + ' + EXTRA if not req and (HERE / 'requirements' / EXTRA).is_file() else ''}, 다른 것 {differ or '없음'}")
    lines.append(f"V-14: {'일치' if ok else '불일치'} (packrt 저장소 {pr['tag']} {pr['commit'][:12]})")
    return ok, lines


def main():
    if Path(sys.prefix).resolve() != (HERE / ".venv").resolve():
        print(f"불일치: 전용 환경이 아님(sys.prefix={sys.prefix})")
        return 1
    pin = json.loads((HERE / "pinned.json").read_text())
    ok, lines = check(pin, HERE / ".venv", Path(os.environ.get("PACKRT_REPO", HERE.parent / "agent-pack-runtime")),
                      Path(os.environ.get("LLMOPS_REPO", HERE.parent / "llmops-lite-v2")))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
