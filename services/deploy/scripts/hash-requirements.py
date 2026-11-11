"""VPS deploy 이미지의 해시 고정 목록(지시 036 H1, D-152 ③): requirements/deploy-venv.txt의 이미지 줄마다 uv.lock들의 wheel sha256을 붙인다.
- 이미지 줄 = deploy.Dockerfile /tmp/deploy-req.txt와 같은 것(packrt · tracewriter · 시험용 4개를 뺌). 판은 다시 해석하지 않음
- 해시 = 주어진 lock들의 registry 배포본 중 같은 이름 · 판의 wheel에서 VPS에 맞는 것만(sdist · 다른 플랫폼은 빼고 — 이미지는 --only-binary :all:)
- 멈춤(종료 1): lock에 없는 줄 · 같은 파일 이름의 해시가 lock마다 다름 · VPS(linux x86_64, CPython 3.14, glibc 2.36)에 맞는 wheel 없음
- 네트워크 없음. lock은 파일로 받음(다른 저장소는 git show로 꺼내 넘김). 출력 머리에 lock마다 sha256
사용: scripts/py scripts/hash-requirements.py --lock <이름>=<uv.lock> [...] [--requirements 파일] [--out 파일 | --check 파일]"""
import argparse, hashlib, re, sys, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
EXCLUDE = ("packrt", "tracewriter", "pytest", "pluggy", "iniconfig", "pygments")   # deploy.Dockerfile의 두 grep과 같음
PY, GLIBC = (3, 14), 36


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def image_lines(path):
    """requirements → [(쓴 이름, 판)] 이미지 줄만(순서 그대로)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, v = (x.strip() for x in line.split("==", 1))
            if norm(name) not in EXCLUDE:
                out.append((name, v))
    return out


def lock_wheels(path):
    """uv.lock → {(이름, 판): {wheel 파일 이름: sha256}}(registry 배포본만)."""
    out = {}
    for p in tomllib.loads(Path(path).read_text(encoding="utf-8"))["package"]:
        if "version" in p and (p.get("source") or {}).get("registry"):
            files = out.setdefault((norm(p["name"]), p["version"]), {})
            for w in p.get("wheels", []):
                algo, _, h = w["hash"].partition(":")
                if algo != "sha256":
                    raise ValueError(f"{p['name']} {p['version']}: sha256이 아닌 해시 {algo}")
                files[w["url"].rsplit("/", 1)[1]] = h
    return out


def fits_vps(filename):
    """wheel 파일 이름이 VPS(linux x86_64, CPython 3.14, glibc 2.36)에 맞는지."""
    parts = filename[:-4].split("-")
    if not filename.endswith(".whl") or len(parts) < 5:
        return False
    pys, abis, plats = (set(x.split(".")) for x in parts[-3:])
    any_py = "py3" in pys   # py3 · py2.py3(점으로 나눠 둠)
    cp = f"cp{PY[0]}{PY[1]}"
    abi3 = "abi3" in abis and any(re.fullmatch(r"cp3(\d+)", p) and int(p[3:]) <= PY[1] for p in pys)
    def linux(plat):
        m = re.fullmatch(r"manylinux_2_(\d+)_x86_64", plat)
        return bool(m and int(m.group(1)) <= GLIBC) or plat in ("manylinux1_x86_64", "manylinux2010_x86_64", "manylinux2014_x86_64")
    if "any" in plats:
        return any_py and "none" in abis
    if not any(linux(p) for p in plats):
        return False
    return (cp in pys and cp in abis) or abi3 or (any_py and "none" in abis)


def build(req, locks):
    """(본문, 문제 목록)."""
    tables = {label: lock_wheels(path) for label, path in locks}
    lines, problems = [], []
    for name, v in image_lines(req):
        files = {}
        for label, table in tables.items():
            for fn, h in table.get((norm(name), v), {}).items():
                if files.setdefault(fn, h) != h:
                    problems.append(f"{name}=={v}: {fn} 해시가 lock마다 다름({label})")
        if not files:
            problems.append(f"{name}=={v}: 어느 lock에도 없음")
            continue
        hashes = sorted({h for fn, h in files.items() if fits_vps(fn)})
        if not hashes:
            problems.append(f"{name}=={v}: VPS(linux x86_64 · CPython {PY[0]}.{PY[1]})에 맞는 wheel 없음")
            continue
        lines.append(f"{name}=={v} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes))
    head = ["# 생성: scripts/hash-requirements.py — 손으로 고치지 않음(지시 036 H1). deploy.Dockerfile이 --require-hashes --only-binary :all:로 씀",
            f"# 줄 = requirements/deploy-venv.txt에서 {' · '.join(EXCLUDE)}를 뺀 것(= /tmp/deploy-req.txt). 해시 = 아래 lock들의 wheel 중 VPS(linux x86_64 · CPython 3.14)에 맞는 것의 sha256",
            *(f"# lock {label} sha256 {hashlib.sha256(Path(path).read_bytes()).hexdigest()}" for label, path in locks)]
    return "\n".join(head + lines) + "\n", problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lock", action="append", required=True, metavar="이름=경로")
    ap.add_argument("--requirements", default=str(HERE / "requirements" / "deploy-venv.txt"))
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--out")
    out.add_argument("--check")
    a = ap.parse_args(argv)
    locks = []
    for spec in a.lock:
        label, sep, path = spec.partition("=")
        if not sep or not label or not Path(path).is_file():
            ap.error(f"--lock은 이름=있는 파일 경로: {spec!r}")
        locks.append((label, path))
    text, problems = build(a.requirements, locks)
    if problems:
        print("멈춤 — " + "\n멈춤 — ".join(problems))
        return 1
    if a.check:
        same = Path(a.check).read_text(encoding="utf-8") == text
        print("같음" if same else f"다름 — {a.check}를 다시 만드세요")
        return 0 if same else 1
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
