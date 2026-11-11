"""V-35 · V-38(VFE EV-005 ES-D, CHG-01 · CHG-07): packrt 저장소 태그 설치 · 고정 확인을 가짜 packrt 태그 저장소로 본다.
- install-venv.sh: annotated 태그만 받음(가지 · commit · 없는 태그 · lightweight 태그는 종료 2), 태그 → commit, 제3자 제약에서
  packrt가 `==`로 고정한 판을 바꿈. 가짜 uv(export · compile · venv · pip install/check/freeze/list를 흉내)로 설치 경로까지 돌림 —
  실제 설치 · 내려받기는 0. 새 패키지 종류 = 설치 전 종료 3, export · packrt 설치 실패 = 멈춤, 실패 때 기록 그대로.
- check-pin.check(): 태그 다시 찍힘 · 파일 바뀜 · 편집 설치 · 고정 판 다름 · uv.lock 다름 · 설치 판 다름이면 불일치."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import io
from pathlib import Path

import pytest

from conftest import ROOT

VENV = Path(os.environ.get("DEPLOY_VENV") or ROOT / ".venv")   # scripts/py와 같은 venv(worktree엔 .venv가 없음)

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
PACKRT_PYPROJECT = """[project]
name = "packrt"
dependencies = [
  "langgraph==1.2.13",
  "Langsmith==0.14.4",   # 짝 고정
  "pyyaml>=6.0.3",
]
"""
TRACEWRITER_PYPROJECT = """[project]
name = "tracewriter"
dependencies = [
  "psycopg[binary]>=3.2",
]
"""
EXPORT = """# uv export 머리
fastapi==0.142.2
langgraph==1.2.12
langsmith==0.14.2
colorama==0.4.6 ; sys_platform == 'win32'
psycopg-binary==3.3.1 ; implementation_name != 'pypy'
"""
BEFORE = "fastapi==0.142.2\nlanggraph==1.2.12\nlangsmith==0.14.2\npacktools==1.0\npackrt==0.8.0  # 옛\ntracewriter==0.1.1  # 옛\n"
RESOLVED = "fastapi==0.142.2\nlanggraph==1.2.13\nlangsmith==0.14.4\n"
FREEZE = "fastapi==0.142.2\nlanggraph==1.2.13\nlangsmith==0.14.4\npackrt @ file:///w/src/packrt\ntracewriter @ file:///w/src/tracewriter\n"
LIST = json.dumps([{"name": "fastapi", "version": "0.142.2"}, {"name": "langgraph", "version": "1.2.13"}, {"name": "langsmith", "version": "0.14.4"},
                   {"name": "packrt", "version": "0.9.0"}, {"name": "tracewriter", "version": "0.1.1"}])
FAKE_UV = r"""#!/bin/sh
echo "uv $*" >> "$UV_LOG"
case "$1 $2" in
  "export "*) [ -n "${FAIL_EXPORT:-}" ] && exit 1; exec cat "$FAKE_DIR/export.txt";;
  "venv "*) mkdir -p "$2"; exit 0;;
  "pip compile") exec cat "$FAKE_DIR/resolved.txt";;
  "pip install") for a in "$@"; do case "$a" in */src/packrt) [ -n "${FAIL_BUILD:-}" ] && { echo "build failed"; exit 1; }
    [ -n "${FAKE_BUILD_LOG:-}" ] && cat "$FAKE_BUILD_LOG"; [ -n "${UV_BUILD_CONSTRAINT:-}" ] && echo "build-constraint: $(cat "$UV_BUILD_CONSTRAINT")";; esac; done; exit 0;;
  "pip check") echo "All installed packages are compatible"; exit 0;;
  "pip freeze") exec cat "$FAKE_DIR/freeze.txt";;
  "pip list") exec cat "$FAKE_DIR/list.json";;
esac
exit 99
"""


def git(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repos(tmp_path):
    prt = tmp_path / "agent-pack-runtime"
    write(prt / "packrt/pyproject.toml", PACKRT_PYPROJECT)
    write(prt / "packrt/packrt/__init__.py", '__version__ = "0.9.0"\n')
    write(prt / "packrt/packrt/ir/schema.json", "{}\n")
    write(prt / "tracewriter/pyproject.toml", TRACEWRITER_PYPROJECT)
    write(prt / "tracewriter/tracewriter/__init__.py", '__version__ = "0.1.1"\n')
    git(tmp_path, "init", "-q", "-b", "main", str(prt))
    git(prt, "add", "-A")
    git(prt, "commit", "-q", "-m", "c1")
    git(prt, "tag", "-a", "v0.9.0", "-m", "agent-pack-runtime v0.9.0")
    git(prt, "branch", "rel")
    llm = tmp_path / "llmops-lite-v2"
    write(llm / "pyproject.toml", "[project]\nname = 'llmops'\n")
    home = next(l.split("=", 1)[1].strip() for l in (VENV / "pyvenv.cfg").read_text().splitlines() if l.startswith("home"))
    major = "python" + ".".join(map(str, sys.version_info[:2]))
    (llm / ".venv/bin").mkdir(parents=True)
    (llm / ".venv/bin/python").symlink_to(Path(home) / major)               # llmops .venv 흉내(base 인터프리터만 빌림, 설치 없음)
    write(llm / ".venv/pyvenv.cfg", f"home = {home}\n")
    write(llm / ".gitignore", ".venv/\n")
    write(llm / "uv.lock", "version = 1\n")
    git(tmp_path, "init", "-q", "-b", "main", str(llm))
    git(llm, "add", "-A")
    git(llm, "commit", "-q", "-m", "lock")
    bin_dir, fake = tmp_path / "bin", tmp_path / "fake"
    for name, text in (("export.txt", EXPORT), ("resolved.txt", RESOLVED), ("freeze.txt", FREEZE), ("list.json", LIST)):
        write(fake / name, text)
    write(bin_dir / "uv", FAKE_UV)
    (bin_dir / "uv").chmod(0o755)
    return {"prt": prt, "llm": llm, "bin": bin_dir, "fake": fake, "tmp": tmp_path, "lock_commit": git(llm, "rev-parse", "HEAD")}


def plan(r, tag, plan_only=True, lock=None, **extra):
    """install-venv.sh를 가짜 uv로 돌린다. 기록은 out/, 기준선(requirements/deploy-venv.txt)은 가짜 HERE의 것."""
    here = r["tmp"] / "here"
    if not (here / "scripts").exists():
        (here / "scripts").mkdir(parents=True)
        shutil.copy(ROOT / "scripts/install-venv.sh", here / "scripts/install-venv.sh")
        write(here / "requirements/deploy-venv.txt", BEFORE)
    env = {**os.environ, "PATH": f"{r['bin']}{os.pathsep}{os.environ['PATH']}", "UV_LOG": str(r["tmp"] / "uv.log"),
           "FAKE_DIR": str(r["fake"]), "PACKRT_REPO": str(r["prt"]), "LLMOPS_REPO": str(r["llm"]),
           "DEPLOY_WORK_DIR": str(r["tmp"]), "DEPLOY_VENV_DIR": str(r["tmp"] / "venv"), "DEPLOY_PIN_OUT": str(r["tmp"] / "out"), **extra}
    if plan_only:
        env["INSTALL_PLAN_ONLY"] = "1"
    return subprocess.run(["sh", str(here / "scripts/install-venv.sh"), tag, lock or r["lock_commit"]], capture_output=True, text=True, env=env)


def uv_calls(r):
    log = r["tmp"] / "uv.log"
    return log.read_text().splitlines() if log.exists() else []


def test_install_plan_uses_tag_commit_and_packrt_pins(repos):
    res = plan(repos, "v0.9.0")
    assert res.returncode == 0, res.stderr
    commit = git(repos["prt"], "rev-parse", "v0.9.0^{commit}")
    assert f"태그 v0.9.0 → commit {commit}" in res.stdout
    cons = res.stdout.split("--- 제약\n", 1)[1].splitlines()
    assert "langgraph==1.2.13" in cons and "Langsmith==0.14.4" in cons                       # packrt가 고정한 판(V-38)
    assert not [c for c in cons if c.startswith(("langgraph==1.2.12", "langsmith==0.14.2"))]  # 옛 판은 빠짐(이름은 대소문자 무시)
    assert "fastapi==0.142.2" in cons and "colorama==0.4.6 ; sys_platform == 'win32'" in cons
    assert "psycopg-binary==3.3.1 ; implementation_name != 'pypy'" in cons                     # >= 의존은 제3자 판 그대로
    assert [c.split()[1] for c in uv_calls(repos)] == ["export"]                              # 설치 · venv 만들기 0
    assert not (repos["tmp"] / "venv").exists() and not (repos["tmp"] / "out").exists()
    assert not list(repos["tmp"].glob("deploy-venv.*"))                                       # 작업 폴더 지움


@pytest.mark.parametrize("name", ["rel", "main", "HEAD", "v9.9.9", "commit"])
def test_install_refuses_non_tag(repos, name):
    if name == "commit":
        name = git(repos["prt"], "rev-parse", "HEAD")
    res = plan(repos, name)
    assert res.returncode == 2 and "태그만 받음" in res.stderr
    assert uv_calls(repos) == []


def test_install_refuses_lightweight_tag_and_bad_lock(repos):
    git(repos["prt"], "tag", "v0.9.1")                                     # lightweight
    res = plan(repos, "v0.9.1")
    assert res.returncode == 2 and "annotated" in res.stderr
    for lock in ("main", "HEAD", "abc123"):
        res = plan(repos, "v0.9.0", lock=lock)
        assert res.returncode == 2 and "40자 hex" in res.stderr
    assert uv_calls(repos) == []


def test_install_full_path_writes_records_after_checks(repos):
    res = plan(repos, "v0.9.0", plan_only=False)
    assert res.returncode == 0, res.stderr
    assert "새 패키지 종류: 없음" in res.stdout
    calls = [" ".join(c.split()[1:3]) for c in uv_calls(repos)]
    assert calls[:3] == ["export --frozen", "pip compile", "venv " + str(repos["tmp"] / "venv")] and "pip check" in calls
    pin = json.loads((repos["tmp"] / "out/pinned.json").read_text())
    commit = git(repos["prt"], "rev-parse", "v0.9.0^{commit}")
    assert pin["packrt_repo"] == {"name": "agent-pack-runtime", "tag": "v0.9.0", "tag_object": git(repos["prt"], "rev-parse", "v0.9.0"), "commit": commit}
    assert pin["fixed_by_packrt"] == {"langgraph": "1.2.13", "Langsmith": "0.14.4"} and pin["packrt"]["version"] == "0.9.0"
    assert pin["third_party"]["llmops_commit"] == repos["lock_commit"]
    req = (repos["tmp"] / "out/requirements/deploy-venv.txt").read_text().splitlines()
    assert f"packrt==0.9.0  # agent-pack-runtime v0.9.0 {commit}" in req and "langgraph==1.2.13" in req


def test_install_keeps_repo_url_and_records_the_selected_setuptools(repos):
    """10-21(지시 042): 지난 기록의 packrt_repo.url은 이어받고, 빌드 backend는 지난 build_isolation 판으로 묶어(UV_BUILD_CONSTRAINT)
    uv가 고른 판(`Selecting: setuptools==…`)을 기록 — 색인의 옛 파일 이름(setuptools-0.6b1…)을 잡지 않음."""
    plan(repos, "v0.9.0")   # here/ 만들기
    here = repos["tmp"] / "here"
    write(here / "pinned.json", json.dumps({"packrt_repo": {"name": "agent-pack-runtime", "url": "https://example.invalid/prt.git"},
                                            "build_isolation": "setuptools==84.0.0"}, indent=1))   # 실제 기록과 같은 한 칸 한 줄
    write(repos["fake"] / "build.log", 'DEBUG Skipping file for setuptools: "setuptools-0.6b1-py2.3.egg" (not a wheel)\n'
                                       "DEBUG Selecting: setuptools==84.0.0 [compatible] (setuptools-84.0.0-py3-none-any.whl)\n")
    res = plan(repos, "v0.9.0", plan_only=False, FAKE_BUILD_LOG=str(repos["fake"] / "build.log"))
    assert res.returncode == 0, res.stderr
    assert "build-constraint: setuptools==84.0.0" in res.stderr
    pin = json.loads((repos["tmp"] / "out/pinned.json").read_text())
    assert pin["packrt_repo"]["url"] == "https://example.invalid/prt.git" and pin["build_isolation"] == "setuptools==84.0.0"


def test_install_new_kind_stops_before_install(repos):
    write(repos["fake"] / "resolved.txt", RESOLVED + "brand-new-pkg==1.0\n")
    res = plan(repos, "v0.9.0", plan_only=False)
    assert res.returncode == 3 and "brand-new-pkg" in res.stderr and "설치 전 멈춤" in res.stderr
    assert not [c for c in uv_calls(repos) if c.split()[1:3] in (["pip", "install"], ["venv", str(repos["tmp"] / "venv")])]
    assert not (repos["tmp"] / "out").exists()


def test_install_new_kind_after_install_keeps_records(repos):
    write(repos["fake"] / "freeze.txt", FREEZE + "sneaky==2.0\n")
    res = plan(repos, "v0.9.0", plan_only=False)
    assert res.returncode == 3 and "sneaky" in res.stderr and "기록은 바꾸지 않음" in res.stderr
    assert not (repos["tmp"] / "out").exists()


def test_install_mac_extra_kinds_stay_out_of_the_record(repos):
    """KS-3 맥 전용 덧목록(requirements/onnx-extra.txt)으로 넣은 종류는 venv에 있어도 기록 · 새 종류 확인에서 뺌(D-307 다시 고정 때).
    덧목록에 없는 새 종류는 그대로 멈춤."""
    plan(repos, "v0.9.0")   # here/ 만들기
    write(repos["tmp"] / "here/requirements/onnx-extra.txt",
          "# 맥 전용\nonnxruntime==1.31.0 \\\n    --hash=sha256:aa\nfastapi==0.142.2 \\\n    --hash=sha256:bb\n")
    write(repos["fake"] / "freeze.txt", FREEZE + "onnxruntime==1.31.0\n")
    res = plan(repos, "v0.9.0", plan_only=False)
    assert res.returncode == 0, res.stderr
    req = (repos["tmp"] / "out/requirements/deploy-venv.txt").read_text().splitlines()
    assert not [l for l in req if l.startswith("onnxruntime")] and "fastapi==0.142.2" in req   # 기준선에도 있는 이름은 기록에 남음
    write(repos["fake"] / "freeze.txt", FREEZE + "onnxruntime==1.31.0\nsneaky==2.0\n")
    res = plan(repos, "v0.9.0", plan_only=False, DEPLOY_PIN_OUT=str(repos["tmp"] / "out2"))
    assert res.returncode == 3 and "sneaky" in res.stderr and "onnxruntime" not in res.stderr.split("새 패키지 종류:")[-1]
    assert not (repos["tmp"] / "out2").exists()


@pytest.mark.parametrize("fail", ["FAIL_EXPORT", "FAIL_BUILD"])
def test_install_failures_stop_without_records(repos, fail):
    res = plan(repos, "v0.9.0", plan_only=False, **{fail: "1"})
    assert res.returncode != 0
    assert not (repos["tmp"] / "out").exists()
    if fail == "FAIL_EXPORT":
        assert [c.split()[1] for c in uv_calls(repos)] == ["export"]


def test_install_empty_export_stops(repos):
    write(repos["fake"] / "export.txt", "# 머리만\n")
    res = plan(repos, "v0.9.0")
    assert res.returncode == 1 and "비었습니다" in res.stderr


@pytest.mark.parametrize("deps,want", [
    ("dependencies = ['langgraph==1.2.13', 'langsmith == 0.14.4']", ["langgraph==1.2.13", "langsmith==0.14.4"]),
    ('dependencies = [\n  "langgraph[x]==1.2.13",\n  "langsmith==0.14.4",\n  "pyyaml>=6",\n]', ["langgraph==1.2.13", "langsmith==0.14.4"]),
])
def test_install_pin_parsing(repos, deps, want):
    prt = repos["prt"]
    write(prt / "packrt/pyproject.toml", f'[project]\nname = "packrt"\n{deps}\n\n[project.optional-dependencies]\ntest = ["pytest==1.0"]\n')
    git(prt, "commit", "-qam", "deps")
    git(prt, "tag", "-a", "v0.9.2", "-m", "t")
    res = plan(repos, "v0.9.2")
    assert res.returncode == 0, res.stderr
    cons = res.stdout.split("--- 제약\n", 1)[1].splitlines()
    assert cons[-len(want):] == want and not [c for c in cons if c.startswith("pytest")]


def test_install_pin_with_marker_stops(repos):
    prt = repos["prt"]
    write(prt / "packrt/pyproject.toml", '[project]\nname = "packrt"\ndependencies = ["langgraph==1.2.13", "langsmith==0.14.4; python_version < \'3.10\'"]\n')
    git(prt, "commit", "-qam", "deps")
    git(prt, "tag", "-a", "v0.9.4", "-m", "t")
    res = plan(repos, "v0.9.4")
    assert res.returncode == 2 and "조건(마커)" in res.stderr
    assert uv_calls(repos) == []


def test_check_pin_tree_path_missing(installed):
    installed["pin"]["packrt"]["tree"] = "0" * 40                          # tree 다름 → 불일치 줄(traceback 없음)
    ok, lines = run_check(installed)
    assert not ok and "tree 다름" in lines[0]


def test_install_missing_langsmith_pin_stops(repos):
    prt = repos["prt"]
    write(prt / "packrt/pyproject.toml", '[project]\nname = "packrt"\ndependencies = ["langgraph==1.2.13", "langsmith>=0.14"]\n')
    git(prt, "commit", "-qam", "deps")
    git(prt, "tag", "-a", "v0.9.3", "-m", "t")
    res = plan(repos, "v0.9.3")
    assert res.returncode == 2 and "langsmith== 고정이 없습니다" in res.stderr
    assert [c.split()[1] for c in uv_calls(repos)] == []


class FakeDist:
    def __init__(self, base, name, version, editable=False):
        self.base, self.name, self.version, self.editable = base, name, version, editable

    def read_text(self, f):
        return json.dumps({"dir_info": {"editable": self.editable}}) if f == "direct_url.json" else None

    def locate_file(self, p):
        return self.base / p


def _check_pin():
    spec = importlib.util.spec_from_file_location("check_pin", ROOT / "scripts" / "check-pin.py")
    mod = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    return mod


@pytest.fixture
def installed(repos):
    """가짜 venv에 태그 archive의 packrt · tracewriter 파일을 풀고, 그 태그 기록의 pin을 만든다."""
    prt, venv = repos["prt"], repos["tmp"] / "venv"
    site = venv / "lib/site-packages"
    commit = git(prt, "rev-parse", "v0.9.0^{commit}")
    for name in ("packrt", "tracewriter"):
        data = subprocess.run(["git", "-C", str(prt), "archive", commit, f"{name}/{name}"], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            for m in tf.getmembers():
                if m.isfile():
                    write(site / m.name[len(name) + 1:], tf.extractfile(m).read().decode())
    lock = subprocess.run(["git", "-C", str(repos["llm"]), "show", "HEAD:uv.lock"], capture_output=True, check=True).stdout
    pin = {"packrt_repo": {"name": "agent-pack-runtime", "tag": "v0.9.0", "tag_object": git(prt, "rev-parse", "v0.9.0"), "commit": commit},
           "packrt": {"version": "0.9.0", "tree": git(prt, "rev-parse", f"{commit}:packrt")},
           "tracewriter": {"version": "0.1.1", "tree": git(prt, "rev-parse", f"{commit}:tracewriter")},
           "fixed_by_packrt": {"langgraph": "1.2.13", "langsmith": "0.14.4"},
           "third_party": {"llmops_commit": repos["lock_commit"], "uv_lock_sha256": hashlib.sha256(lock).hexdigest()}}
    vers = {"langgraph": "1.2.13", "langsmith": "0.14.4"}
    write(repos["tmp"] / "req.txt", "langgraph==1.2.13\nlangsmith==0.14.4\npackrt==0.9.0  # 태그\n")
    have = {"langgraph": "1.2.13", "langsmith": "0.14.4", "packrt": "0.9.0"}
    dists = {"packrt": FakeDist(site, "packrt", "0.9.0"), "tracewriter": FakeDist(site, "tracewriter", "0.1.1")}
    return {"mod": _check_pin(), "pin": pin, "venv": venv, "site": site, "vers": vers, "dists": dists, "have": have, **repos}


def run_check(i):
    def dist(n):
        if n not in i["dists"]:
            raise i["mod"].PackageNotFoundError(n)
        return i["dists"][n]
    return i["mod"].check(i["pin"], i["venv"], i["prt"], i["llm"], dist=dist, ver=lambda n: i["vers"][n],
                          installed=lambda: i["have"], req=i["tmp"] / "req.txt")


def test_check_pin_matches_tag_install(installed):
    ok, lines = run_check(installed)
    assert ok, lines
    assert lines[-1].startswith("V-14: 일치 (packrt 저장소 v0.9.0")
    assert any(l.startswith("langgraph: 일치 — 설치 1.2.13") for l in lines)


def test_check_pin_retagged(installed):
    prt = installed["prt"]
    write(prt / "packrt/packrt/__init__.py", '__version__ = "0.9.0"\nX = 1\n')
    git(prt, "commit", "-qam", "c2")
    git(prt, "tag", "-f", "-a", "v0.9.0", "-m", "다시 찍음")
    ok, lines = run_check(installed)
    assert not ok and lines[0].startswith("태그: 불일치")


def test_check_pin_retagged_same_commit(installed):
    git(installed["prt"], "tag", "-f", "-a", "v0.9.0", "v0.9.0^{commit}", "-m", "같은 commit에 다시 찍음")
    ok, lines = run_check(installed)
    assert not ok and lines[0].startswith("태그: 불일치")


def test_check_pin_tag_missing(installed):
    git(installed["prt"], "tag", "-d", "v0.9.0")
    ok, lines = run_check(installed)
    assert not ok and "→ 없음" in lines[0]


def test_check_pin_file_changed(installed):
    (installed["site"] / "packrt/ir/schema.json").write_text('{"x": 1}\n')
    ok, lines = run_check(installed)
    assert not ok and "packrt/ir/schema.json" in next(l for l in lines if l.startswith("packrt:"))


@pytest.mark.parametrize("what", ["editable", "version", "langsmith", "lock", "lock_commit", "missing", "third_party", "extra"])
def test_check_pin_mismatch(installed, what):
    if what == "editable":
        installed["dists"]["tracewriter"].editable = True
    elif what == "version":
        installed["dists"]["packrt"].version = "0.8.0"
    elif what == "langsmith":
        installed["vers"]["langsmith"] = "0.14.2"
    elif what == "lock":
        installed["pin"]["third_party"]["uv_lock_sha256"] = "0" * 64
    elif what == "lock_commit":
        installed["pin"]["third_party"]["llmops_commit"] = "f" * 40               # 없는 commit — traceback 대신 불일치
    elif what == "missing":
        del installed["dists"]["tracewriter"]
    elif what == "third_party":
        installed["have"]["langgraph"] = "1.2.12"
    else:
        installed["have"]["sneaky"] = "1.0"
    ok, lines = run_check(installed)
    assert not ok and lines[-1].startswith("V-14: 불일치")


def test_check_pin_outside_venv(installed, tmp_path):
    other = tmp_path / "venv-old"                                         # 이름 앞부분이 같아도 밖
    shutil.copytree(installed["site"], other)
    installed["dists"]["packrt"].base = other
    ok, lines = run_check(installed)
    assert not ok and "위치 .venv 안 False" in next(l for l in lines if l.startswith("packrt:"))


def test_pinned_json_records_tag():
    pin = json.loads((ROOT / "pinned.json").read_text(encoding="utf-8"))
    assert pin["packrt_repo"]["tag"] == "v0.15.0" and pin["packrt_repo"]["commit"].startswith("d3bc887a4e4b")
    assert pin["fixed_by_packrt"] == {"langgraph": "1.2.13", "langsmith": "0.14.4"}
    req = (ROOT / "requirements/deploy-venv.txt").read_text(encoding="utf-8").splitlines()
    assert "langgraph==1.2.13" in req and "langsmith==0.14.4" in req
    assert any(l.startswith(f"packrt=={pin['packrt']['version']}  # agent-pack-runtime v0.15.0 ") for l in req)
