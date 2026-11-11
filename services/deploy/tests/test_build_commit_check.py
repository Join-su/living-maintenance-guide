"""F-10 빌드 commit 대조(VFE EV-005 ES-G 준비, 지시 029): Docker 없이 대조 함수 · 스크립트 · Dockerfile 셸 줄을 시험한다.
① 설치 전(deploy.Dockerfile 셸 줄): pinned.json commit으로 설치(태그로 설치하지 않음) — 시험 폴더 pinned.json · 가짜 pip로 그 줄을 그대로 돌림
  설치 뒤(scripts/build_commit_check.py, 보조): 설치된 packrt · tracewriter direct_url.json vcs_info.commit_id = pinned.json packrt_repo.commit
② deploy.Dockerfile 셸 줄 — 받은 deploy 태그 commit(clone HEAD) = 빌드 인자 DEPLOY_COMMIT(시험 폴더 git 저장소에 그 줄을 그대로 돌림)"""
import json
import os
import re
import subprocess
import sys

import pytest
import yaml

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import build_commit_check as bcc  # noqa: E402

PIN = json.loads((ROOT / "pinned.json").read_text(encoding="utf-8"))
COMMIT, TAG = PIN["packrt_repo"]["commit"], PIN["packrt_repo"]["tag"]
DOCKERFILE = (ROOT / "docker" / "deploy.Dockerfile").read_text(encoding="utf-8")


def direct(name, commit=COMMIT, rev=COMMIT, url="ssh://git@github.com/Join-su/agent-pack-runtime.git", **over):
    d = {"url": url, "vcs_info": {"vcs": "git", "requested_revision": rev, "commit_id": commit}, "subdirectory": name}
    d.update(over)
    return json.dumps(d)


def reader(**per_name):
    return lambda name: per_name.get(name, direct(name))


# ---------------------------------------------------------------- ① 대조 함수
def test_same_commit_passes():
    ok, lines = bcc.check_packrt(PIN, TAG, read=reader())
    assert ok and len(lines) == 2 and all("같음" in l for l in lines)


@pytest.mark.parametrize("bad, why", [
    (direct("tracewriter", commit="1" * 40), "≠ pinned.json"),                                     # 다른 commit(태그 바꿔치기)
    (direct("tracewriter", commit=COMMIT[:12]), "40자 commit이 아님"),                              # 짧은 번호
    (json.dumps({"url": "file:///x", "dir_info": {}}), "vcs_info.commit_id 칸 없음"),               # 칸 없음(git 태그 설치가 아님)
    (json.dumps({"url": "x", "vcs_info": {"vcs": "git"}}), "vcs_info.commit_id 칸 없음"),
    (None, "direct_url.json 없음"),
    ("{not json", "JSON이 아님"),
    (direct("tracewriter", rev="main"), "설치에 쓴 판"),                                            # 가지 이름으로 설치
    (direct("tracewriter", rev=TAG), "설치에 쓴 판"),                                               # 태그로 설치(commit 고정이 빠짐)
    (direct("tracewriter", commit=12345), "글자가 아님"),
    (direct("tracewriter", subdirectory="packrt"), "하위 폴더"),
    (direct("tracewriter", url="ssh://git@github.com/someone/agent-pack-runtime.git"), "설치 저장소"),
    (direct("tracewriter", url="ssh://git@evil.example/Join-su/agent-pack-runtime.git"), "설치 저장소"),     # 호스트가 다름
])
def test_mismatch_or_missing_fails(bad, why):
    ok, lines = bcc.check_packrt(PIN, TAG, read=reader(tracewriter=bad))
    assert not ok and lines[0].startswith("packrt: 같음") and why in lines[1]


def test_pinned_commit_must_be_40_chars():
    pin = {**PIN, "packrt_repo": {**PIN["packrt_repo"], "commit": COMMIT[:7]}}
    ok, lines = bcc.check_packrt(pin, TAG, read=reader())
    assert not ok and all("pinned.json packrt_repo.commit이 40자 commit이 아님" in l for l in lines)


@pytest.mark.parametrize("tag", ["", "v0.9.0", None])
def test_build_tag_must_equal_pinned_tag(tag):
    ok, lines = bcc.check_packrt(PIN, tag, read=reader())
    assert not ok and lines == [f"멈춤 — 빌드 인자 PACKRT_TAG {tag!r} ≠ pinned.json packrt_repo.tag {TAG!r}"]


@pytest.mark.parametrize("url", ["https://github.com/Join-su/agent-pack-runtime.git", "git+ssh://git@github.com/Join-su/agent-pack-runtime.git",
                                 "git@github.com:Join-su/agent-pack-runtime.git", "ssh://git@github.com/Join-su/agent-pack-runtime"])
def test_repo_url_forms_are_the_same_repo(url):
    assert bcc.repo_path(url) == "github.com/join-su/agent-pack-runtime"
    assert bcc.check_packrt(PIN, TAG, read=reader(packrt=direct("packrt", url=url)))[0]


def test_main_fails_closed_on_a_local_copy_install(monkeypatch, capsys):
    """git 태그 설치가 아닌 사본 설치(예: Mac 전용 .venv의 file:// — vcs_info 없음)는 '칸 없음'으로 멈춤(종료 1)."""
    monkeypatch.setattr(bcc, "read_direct_url", lambda name: json.dumps({"url": "file:///x", "dir_info": {}}))
    assert bcc.main(["packrt", "--pinned", str(ROOT / "pinned.json"), "--tag", TAG]) == 1
    out = capsys.readouterr().out
    assert "F-10 ① 다름" in out and "vcs_info.commit_id 칸 없음" in out
    monkeypatch.setattr(bcc, "read_direct_url", lambda name: direct(name))
    assert bcc.main(["packrt", "--pinned", str(ROOT / "pinned.json"), "--tag", TAG]) == 0


def test_script_usage_error_is_2(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_commit_check.py"), "packrt", "--pinned", str(tmp_path / "none.json"),
                        "--tag", TAG], capture_output=True, text=True)
    assert r.returncode == 2 and "사용 오류" in r.stderr


# ---------------------------------------------------------------- Dockerfile 단계(문자열)
def run_lines():
    """RUN 줄(이어진 줄 붙임) 목록과 그 앞 주석."""
    out, cur = [], None
    for line in DOCKERFILE.splitlines():
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


def test_dockerfile_order_clone_check2_install_check1():
    build = DOCKERFILE[:DOCKERFILE.rindex("FROM ${BASE_IMAGE}")]
    at = lambda s: build.index(step(s))                                                     # RUN 줄 자체의 자리(주석이 아니라)
    assert "ARG DEPLOY_COMMIT" in build
    assert at("git clone --depth 1") < at("rev-parse HEAD") < at("-r /src/deploy/requirements/deploy-image.hashes.txt") \
        < at("-r /src/deploy/requirements/onnx-extra-linux.hashes.txt") < at("#subdirectory=tracewriter") < at("build_commit_check.py packrt")
    install = step("#subdirectory=tracewriter")
    assert "@${PACKRT_TAG}" not in install and install.count("@${pin}#subdirectory=") == 2   # 태그가 아니라 pinned commit으로 설치
    two = step("rev-parse HEAD")
    assert "/src/deploy/scripts" not in two and "python" not in two                         # 받은 코드를 믿기 전: 셸만
    one = step("build_commit_check.py")
    assert one == 'RUN /opt/venv/bin/python /src/deploy/scripts/build_commit_check.py packrt --pinned /src/deploy/pinned.json --tag "$PACKRT_TAG"'


def test_compose_passes_deploy_commit_to_the_deploy_build():
    c = yaml.safe_load((ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8"))
    for name, svc in c["services"].items():
        if name.startswith("deploy-"):
            assert svc["build"]["args"]["DEPLOY_COMMIT"] == "${DEPLOY_COMMIT:-}", name


# ---------------------------------------------------------------- ② Dockerfile 셸 줄을 그대로 돌림(시험 git 저장소)
@pytest.fixture
def repo(tmp_path):
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path / "deploy"), *a], capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "deploy").mkdir()
    g("init", "-q")
    (tmp_path / "deploy" / "f").write_text("x")
    g("add", "f")
    g("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "t")
    return tmp_path / "deploy", g("rev-parse", "HEAD")


def check2(repo_dir, commit):
    script = re.sub(r"^RUN ", "", step("rev-parse HEAD")).replace("\\\n", "").replace("/src/deploy", str(repo_dir))
    env = {"PATH": "/usr/bin:/bin", "DEPLOY_COMMIT": commit, "DEPLOY_TAG": "v-test"}
    return subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, env=env)


def test_check2_same_commit_passes(repo):
    d, head = repo
    r = check2(d, head)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("commit, why", [("0" * 40, "≠ DEPLOY_COMMIT"), (None, "40자 commit"), ("", "40자 commit"), ("ABC" * 13 + "D", "40자 commit")])
def test_check2_other_short_or_empty_fails(repo, commit, why):
    d, head = repo
    r = check2(d, head[:12] if commit is None else commit)
    assert r.returncode != 0 and why in r.stdout and "F-10 ②" in r.stdout


def test_check2_multiline_commit_stops_at_length(repo):   # packrt 보고 020c L0: 여러 줄 값이 grep -x를 통과하던 틈 — 길이 40 검사로 막음
    d, head = repo
    r = check2(d, head + "\nzz")
    assert r.returncode != 0 and "40자 commit" in r.stdout and "F-10 ②" in r.stdout


def test_check2_not_a_repo_fails(tmp_path):
    r = check2(tmp_path / "missing", "a" * 40)
    assert r.returncode != 0


# ---------------------------------------------------------------- ① 설치 전 셸 줄을 그대로 돌림(시험 pinned.json · 가짜 pip, 실제 설치 없음)
def check1_install(tmp_path, tag, commit=COMMIT, pin_tag=TAG, raw=None):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pinned.json").write_text(raw if raw is not None else json.dumps({"packrt_repo": {"tag": pin_tag, "commit": commit}}))
    log = tmp_path / "pip.log"
    pip = tmp_path / "pip"
    pip.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {log}\n")
    pip.chmod(0o755)
    text = step("#subdirectory=tracewriter")
    script = re.sub(r"^RUN --mount=\S+ \\\n", "", text).replace("\\\n", "").replace("/src/deploy", str(src)) \
        .replace("/opt/venv/bin/pip", str(pip)).replace("python -I", f"{sys.executable} -I")
    env = {"PATH": "/usr/bin:/bin", "PACKRT_TAG": tag, "PACKRT_REPO": "git+ssh://git@github.com/Join-su/agent-pack-runtime.git"}
    r = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, env=env)
    return r, (log.read_text().splitlines() if log.exists() else None)


def test_check1_installs_the_pinned_commit_not_the_tag(tmp_path):
    r, args = check1_install(tmp_path, TAG)
    assert r.returncode == 0, r.stdout + r.stderr
    assert args == ["install", "--no-deps", "--no-build-isolation", f"packrt @ git+ssh://git@github.com/Join-su/agent-pack-runtime.git@{COMMIT}#subdirectory=packrt",
                    f"tracewriter @ git+ssh://git@github.com/Join-su/agent-pack-runtime.git@{COMMIT}#subdirectory=tracewriter"]


@pytest.mark.parametrize("tag, commit", [("v9.9.9", COMMIT), (TAG, COMMIT[:7]), ("", COMMIT)])
def test_check1_other_tag_or_short_commit_stops_before_pip(tmp_path, tag, commit):
    r, args = check1_install(tmp_path, tag, commit=commit)
    assert r.returncode != 0 and "F-10 ①" in r.stdout and args is None                       # pip(받은 packrt 코드)를 부르지 않음


@pytest.mark.parametrize("raw", [json.dumps({"packrt_repo": {"tag": TAG, "commit": COMMIT + "\nzz"}}),     # 여러 줄 값(독립 확인 2회차 L-B)
                                 json.dumps({"packrt_repo": {"commit": COMMIT}}), "{broken"])
def test_check1_odd_pinned_json_stops_before_pip(tmp_path, raw):
    r, args = check1_install(tmp_path, TAG, raw=raw)
    assert r.returncode != 0 and "F-10 ①" in r.stdout and args is None
