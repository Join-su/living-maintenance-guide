"""VPS deploy 이미지 PyPI 해시 고정(지시 036 H1 + S1, D-152 ③). 네트워크 · Docker 없음.
- 해시 목록 requirements/deploy-image.hashes.txt = deploy-venv.txt 이미지 줄(Dockerfile grep 목록) + VPS wheel sha256(scripts/hash-requirements.py가 uv.lock들에서)
- 빌드 backend setuptools(S1) = pinned.json build_isolation 판, 해시 고정 · packrt · tracewriter는 --no-build-isolation · 운영 이미지에 남기지 않음
- Dockerfile의 대조 줄은 시험 폴더에서 셸로 그대로 돌림"""
import importlib.util
import json
import re
import subprocess

import pytest

from conftest import ROOT, TASK10

LLMOPS = ROOT.parent / "llmops-lite-v2"
DOCKERFILE = (ROOT / "docker" / "deploy.Dockerfile").read_text(encoding="utf-8")
BUILD = DOCKERFILE[:DOCKERFILE.rindex("FROM ${BASE_IMAGE}")]
PIN = json.loads((ROOT / "pinned.json").read_text(encoding="utf-8"))
LOCKS = (("llmops-0867ce0", LLMOPS, PIN["third_party"]["llmops_commit"]),            # 제3자 판 lock(sha = pinned.json)
         ("llmops-829ba33", LLMOPS, "829ba33f16aae5daf9bb9108c4d15cc7065bdff0"),      # langgraph 1.2.13 · langsmith 0.14.4(packrt 고정 판)
         ("task10-pack-15424a3", TASK10, "15424a30db17548f5a3c8003e1e9f8c4e9d78da1"))  # 같은 두 판 — 두 번째 출처
SETUPTOOLS_WHEEL = "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670"  # pypi.org JSON = PEP 691 simple(S1, 10-20)

spec = importlib.util.spec_from_file_location("hash_requirements", ROOT / "scripts" / "hash-requirements.py")
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)


def entries(path):
    """해시 requirements → {name==판: [sha256 …]}(이어진 줄 붙임)."""
    text = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8"))
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            req, *opts = line.split()
            out[req] = [o.removeprefix("--hash=sha256:") for o in opts]
            assert all(o.startswith("--hash=sha256:") for o in opts), line
    return out


def run_lines():
    out, cur = [], None
    for line in BUILD.splitlines():
        if cur is not None:
            cur += "\n" + line
        elif line.startswith("RUN "):
            cur = line
        if cur is not None and not cur.rstrip().endswith("\\"):
            out.append(cur)
            cur = None
    return out


def step(marker):
    found = [r for r in run_lines() if marker in r]
    assert len(found) == 1, marker
    return found[0]


def image_lines():
    return [f"{n}=={v}" for n, v in hr.image_lines(ROOT / "requirements" / "deploy-venv.txt")]


def test_hash_list_lines_are_the_image_lines_with_vps_wheel_hashes():
    got = entries(ROOT / "requirements" / "deploy-image.hashes.txt")
    assert list(got) == image_lines() and len(got) == 51
    assert all(hs and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hs) for hs in got.values())
    assert "langgraph==1.2.13" in got and "langsmith==0.14.4" in got and not [r for r in got if r.split("==")[0] in hr.EXCLUDE]


def test_exclusions_are_the_dockerfile_greps():
    one = step("> /tmp/deploy-req.txt")
    pats = re.findall(r"grep -Ev '\^\(([^)]*)\)=='", one)
    assert tuple(n for p in pats for n in p.split("|")) == hr.EXCLUDE


@pytest.mark.skipif(not (LLMOPS / ".git").exists(), reason=f"llmops 저장소가 없습니다: {LLMOPS}")
def test_hash_list_regenerates_from_the_pinned_locks(tmp_path):
    """세 lock을 git show로만 꺼내(다른 저장소는 읽기만) 다시 만들면 바이트가 같음. 제3자 lock sha256 = pinned.json."""
    args = []
    for label, repo, commit in LOCKS:
        lock = tmp_path / f"{label}.lock"
        lock.write_bytes(subprocess.run(["git", "-C", str(repo), "show", f"{commit}:uv.lock"], capture_output=True, check=True).stdout)
        args += ["--lock", f"{label}={lock}"]
    import hashlib
    assert hashlib.sha256((tmp_path / "llmops-0867ce0.lock").read_bytes()).hexdigest() == PIN["third_party"]["uv_lock_sha256"]
    assert hr.main(args + ["--check", str(ROOT / "requirements" / "deploy-image.hashes.txt")]) == 0


def fake_lock(path, *pkgs):
    rows = []
    for name, version, wheels in pkgs:
        ws = ", ".join(f'{{ url = "https://files.example/{fn}", hash = "sha256:{h}" }}' for fn, h in wheels)
        rows.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {{ registry = "https://pypi.org/simple" }}\nwheels = [{ws}]\n')
    path.write_text("version = 1\n\n" + "\n".join(rows), encoding="utf-8")
    return path


def test_generator_stops_on_conflict_missing_or_no_vps_wheel(tmp_path, capsys):
    req = tmp_path / "req.txt"
    req.write_text("a-b==1.0\nc==2.0\npytest==9.0\npackrt==0.10.0  # 뺌\n", encoding="utf-8")
    pure, mac = "a_b-1.0-py3-none-any.whl", "c-2.0-cp314-cp314-macosx_11_0_arm64.whl"
    one = fake_lock(tmp_path / "1.lock", ("a-b", "1.0", [(pure, "1" * 64)]), ("c", "2.0", [(mac, "2" * 64)]))
    assert hr.main(["--lock", f"x={one}", "--requirements", str(req)]) == 1
    assert "c==2.0: VPS(linux x86_64 · CPython 3.14)에 맞는 wheel 없음" in capsys.readouterr().out
    two = fake_lock(tmp_path / "2.lock", ("a-b", "1.0", [(pure, "9" * 64)]))
    assert hr.main(["--lock", f"x={one}", "--lock", f"y={two}", "--requirements", str(req)]) == 1
    assert f"a-b==1.0: {pure} 해시가 lock마다 다름(y)" in capsys.readouterr().out
    lin = "c-2.0-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    ok = fake_lock(tmp_path / "3.lock", ("a_b", "1.0", [(pure, "1" * 64)]), ("c", "2.0", [(mac, "2" * 64), (lin, "3" * 64)]))
    out = tmp_path / "out.txt"
    assert hr.main(["--lock", f"x={ok}", "--requirements", str(req), "--out", str(out)]) == 0
    assert entries(out) == {"a-b==1.0": ["1" * 64], "c==2.0": ["3" * 64]}          # 맥 wheel 해시는 넣지 않음
    req.write_text("a-b==1.0\nd==1.0\n", encoding="utf-8")
    assert hr.main(["--lock", f"x={ok}", "--requirements", str(req)]) == 1
    assert "d==1.0: 어느 lock에도 없음" in capsys.readouterr().out


@pytest.mark.parametrize("fn, fits", [
    ("x-1-py3-none-any.whl", True), ("x-1-py2.py3-none-any.whl", True),
    ("x-1-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", True),
    ("x-1-cp37-abi3-manylinux1_x86_64.manylinux_2_28_x86_64.whl", True),
    ("x-1-cp314-cp314-manylinux_2_39_x86_64.whl", False),          # bookworm glibc 2.36보다 새것
    ("x-1-cp314-cp314-musllinux_1_2_x86_64.whl", False), ("x-1-cp314-cp314-manylinux_2_17_aarch64.whl", False),
    ("x-1-cp313-cp313-manylinux_2_17_x86_64.whl", False), ("x-1-cp314-cp314t-manylinux_2_17_x86_64.whl", False),
    ("x-1-cp314-cp314-win_amd64.whl", False), ("x-1.tar.gz", False),
])
def test_vps_wheel_tags(fn, fits):
    assert hr.fits_vps(fn) is fits


def test_dockerfile_installs_third_party_by_hash_only():
    """제3자는 해시로만(--require-hashes --only-binary :all:), 그 앞에 name==판 대조. 다른 pip install 줄은 빌드 backend(S1) · packrt · tracewriter(pinned commit)뿐."""
    third = step("-r /src/deploy/requirements/deploy-image.hashes.txt")
    git = step("#subdirectory=tracewriter")
    compare = step("cmp -s - /tmp/hashed.txt")
    assert "--require-hashes" in third and "--only-binary :all:" in third and "--no-deps" in third
    at = lambda s: BUILD.index(s)
    assert at(step("> /tmp/deploy-req.txt")) < at(compare) < at(third) < at(git) < at(step("build_commit_check.py")) < at(step("pip check"))
    onnx = step("-r /src/deploy/requirements/onnx-extra-linux.hashes.txt")                # C1-a15 V-3: 같은 해시 규칙의 덧목록 하나
    assert "--require-hashes" in onnx and "--only-binary :all:" in onnx and "--no-deps" in onnx and at(third) < at(onnx) < at(git)
    assert [r for r in run_lines() if "pip install" in r] == [third, onnx, step("-r /src/deploy/requirements/build-backend.hashes.txt"), git]
    assert "pip install --no-deps -r /tmp/deploy-req.txt" not in BUILD                   # 해시 없는 설치 길이 남지 않음


def test_build_backend_is_the_pinned_setuptools_by_hash():
    got = entries(ROOT / "requirements" / "build-backend.hashes.txt")
    assert got == {PIN["build_isolation"]: [SETUPTOOLS_WHEEL]} and PIN["build_isolation"] == "setuptools==84.0.0"


def test_packrt_builds_with_the_hashed_backend_only():
    """S1(답장 042-01 · 043-01): build-backend 해시 설치 → packrt · tracewriter --no-build-isolation(PyPI 최신 setuptools를 받지 않음)
    → setuptools 지움 → build_commit_check → pip check."""
    backend = step("-r /src/deploy/requirements/build-backend.hashes.txt")
    git = step("#subdirectory=tracewriter")
    assert "--require-hashes" in backend and "--only-binary :all:" in backend and "--no-deps" in backend
    assert "--no-build-isolation" in git and "--no-build-isolation" not in step("-r /src/deploy/requirements/deploy-image.hashes.txt")
    at = lambda s: BUILD.index(s)
    assert at(step("-r /src/deploy/requirements/deploy-image.hashes.txt")) < at(backend) < at(git) < at(step("pip uninstall -y setuptools")) \
        < at(step("build_commit_check.py")) < at(step("pip check"))
    assert "setuptools" not in (ROOT / "requirements" / "deploy-image.hashes.txt").read_text(encoding="utf-8")   # 운영 목록엔 없음


def _run_compare(tmp_path, hashes_text):
    src = tmp_path / "src" / "deploy" / "requirements"
    src.mkdir(parents=True, exist_ok=True)
    (src / "deploy-venv.txt").write_bytes((ROOT / "requirements" / "deploy-venv.txt").read_bytes())
    (src / "deploy-image.hashes.txt").write_text(hashes_text, encoding="utf-8")
    script = "\n".join(re.sub(r"^RUN ", "", s) for s in (step("> /tmp/deploy-req.txt"), step("cmp -s - /tmp/hashed.txt")))
    script = script.replace("/src/deploy", str(tmp_path / "src" / "deploy")).replace("/tmp/", f"{tmp_path}/")
    assert "python -m venv /opt/venv" in script
    script = script.replace("python -m venv /opt/venv \\\n &&", "")                    # venv 만들기는 빼고 대조만
    return subprocess.run(["sh", "-ec", script], capture_output=True, text=True)


def test_dockerfile_compare_line_runs_in_shell(tmp_path):
    real = (ROOT / "requirements" / "deploy-image.hashes.txt").read_text(encoding="utf-8")
    assert _run_compare(tmp_path, real).returncode == 0
    r = _run_compare(tmp_path, real.replace("zstandard==0.25.0", "zstandard==0.25.1"))
    assert r.returncode == 1 and "멈춤" in r.stdout
    r = _run_compare(tmp_path, re.sub(r"^zstandard==.*?(?=\n[^ ]|\Z)", "", real, flags=re.S | re.M))   # 줄 하나 빠짐
    assert r.returncode == 1
