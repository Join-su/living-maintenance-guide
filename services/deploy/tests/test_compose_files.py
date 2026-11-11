"""상주 배포 Compose · Dockerfile · VPS 설정 정적 검사(VFE EV-005 ES-A, V-33). yaml · 글자만 읽고 docker 명령은 부르지 않는다."""
import re
from pathlib import Path

import pytest
import yaml

from conftest import ROOT

DOCKER = ROOT / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
DEPLOYS = [n for n in SERVICES if n.startswith(("deploy-task10-", "deploy-c1a15-"))]   # 배포 서버(deploy-tools는 따로, V-40) — C1-a15 단계 A(D-334)
C1A15 = "deploy-c1a15-v1-pg"
C1A15_RELEASES_RO = "/srv/llmopsagent/c1a15-releases:/srv/llmopsagent/c1a15-releases:ro"
SECRET_LIKE = re.compile(r"(postgres(ql)?://[^\s'\"]+:[^\s'\"]+@)|(password[ \t]*[:=][ \t]*[^\s#])", re.I)


def test_three_kinds_of_services():
    assert {"postgres", "llmops"} <= set(SERVICES) and DEPLOYS
    assert "deploy-task10-v1-pg-local" in DEPLOYS


def test_postgres_has_no_published_ports_and_pinned_image():
    pg = SERVICES["postgres"]
    assert "ports" not in pg
    assert "@sha256:" in pg["image"] and ":latest" not in pg["image"]


@pytest.mark.parametrize("name", ["llmops", *DEPLOYS])
def test_servers_publish_on_loopback_only(name):
    ports = SERVICES[name].get("ports") or []
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports)


@pytest.mark.parametrize("name", ["llmops", *DEPLOYS])
def test_servers_are_built_from_tags_not_latest_images(name):
    svc = SERVICES[name]
    assert "build" in svc and "image" not in svc
    args = svc["build"]["args"]
    if name == "llmops":   # 답장 033-01 · packrt 보고 020c: 태그 + 40자 commit, packrt 판은 llmops uv.lock(PACKRT_TAG 없음) — ES-A 기대값을 바꿈
        assert {"LLMOPS_TAG", "LLMOPS_COMMIT"} <= set(args) and "PACKRT_TAG" not in args
    else:
        assert "PACKRT_TAG" in args and "DEPLOY_TAG" in args


def test_env_files_per_service():
    assert SERVICES["postgres"]["env_file"] == ["/srv/llmopsagent/env/postgres.env"]
    assert SERVICES["llmops"]["env_file"] == ["/srv/llmopsagent/env/llmops-keys.env"]   # 키만 환경 변수, DSN은 파일 mount(지시 032 V-07 · V-08 — ES-A 기대값을 바꿈)
    for n in DEPLOYS:
        assert "env_file" not in SERVICES[n]   # 서버가 설정 env_file 경로를 직접 읽음 — 읽기 전용 mount로 붙임(N1)
        assert "/srv/llmopsagent/env/deploy.env:/run/deploy/deploy.env:ro" in SERVICES[n]["volumes"]


@pytest.mark.parametrize("name", DEPLOYS)
def test_deploy_config_env_file_is_the_mounted_secret(name):
    from deploy_server.settings import load_settings
    live = SERVICES[name]["environment"]["DEPLOY_CONFIG"]
    assert live.startswith("config-live/")                                # 운영 설정(N4 A) — 원본은 이미지의 config/
    cfg = ROOT / live.replace("config-live/", "config/", 1)               # (ES-G0: 이 run ES-A 시험 기대값 보충)
    s = load_settings(cfg)
    same = lambda got, want: Path(got) == Path(want).resolve()   # macOS는 /var → /private/var(설정 읽기가 경로를 풂), 컨테이너에선 그대로
    assert same(s.trace["env_file"], "/run/deploy/deploy.env") and same(s.runtime_db["env_file"], "/run/deploy/deploy.env")
    assert same(s.trace["payload_root"], "/var/lib/payloads") and same(s.data_dir, "/var/lib/deploy/data")
    mounts = {v.split(":")[1] for v in SERVICES[name]["volumes"] if ":" in v}
    assert "/var/lib/status" in mounts and "/var/lib/payloads" in mounts and "/var/lib/deploy/data" in mounts
    assert same(s.pack_repo, f"/var/lib/deploy/pack-cache/{'c1a15' if name == C1A15 else 'task10'}-pack") and "/var/lib/deploy/pack-cache" in mounts
    assert s.registration == {"mode": "llmops_pg"} and "/var/lib/llmops-ledger" not in mounts   # 등록 장부 = PG(ES-E — 이 run ES-A 기대값을 지시 026으로 바꿈)


def test_vps_configs_match_local_deployments_except_paths():
    from deploy_server.settings import load_settings
    for vps, local in (("v1-pg-vps", "v1-pg-local"), ("v0-pg-vps", "v0-pg-local")):
        a, b = load_settings(ROOT / "config" / f"{vps}.yaml"), load_settings(ROOT / "config" / f"{local}.yaml")
        assert (a.deployment_id, a.pack_ref, a.expect_revision, a.execution, a.review_policy, a.provider) == \
               (b.deployment_id, b.pack_ref, b.expect_revision, b.execution, b.review_policy, b.provider)
        same = ("retention_class", "outcome_by_node", "grounding_keys", "secret_env_names", "pack_subdir", "releases_subdir")
        assert all(getattr(a, k) == getattr(b, k) for k in same)
        assert (a.registration["mode"], b.registration["mode"]) == ("llmops_pg", "llmops_pg")   # 실제 장부를 쓰는 설정은 모두 PG(A5 뒤 — 답장 031-01이 ES-E 기대값을 바꿈)


def test_deploy_dockerfile_installs_packrt_from_tag_only():
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    assert "grep -Ev '^(packrt|tracewriter)=='" in d and "git+ssh://git@github.com/" in d    # 공개 PyPI에서 찾지 않음, pip 주소 형식
    assert "chown -R deploy:deploy" in d


def test_volumes_and_status_readonly_for_llmops():
    assert {"pgdata", "llmops-data", "payloads", "status", "pack-cache-deploy"} <= set(COMPOSE["volumes"])
    assert all(f"deploy-data-{n[len('deploy-'):]}" in COMPOSE["volumes"] for n in DEPLOYS)
    assert "status:/var/lib/status:ro" in SERVICES["llmops"]["volumes"]


LLMOPS_ENV = {   # 지시 032 V-09(packrt 보고 019 · vps-prep CHG-V04 이름 목록) — 값은 비밀 아님
    "LLMOPS_HOST": "0.0.0.0", "LLMOPS_PORT": "8765", "LLMOPS_LLM_MODE": "fake", "LLMOPS_STORE": "pg", "LLMOPS_TRACE": "postgres",
    "LLMOPS_DB_ENV_FILE": "/run/llmops/db.env", "LLMOPS_DATA_DIR": "/var/lib/llmops", "LLMOPS_DEPLOY_STATUS_DIR": "/var/lib/status/deployments",
    "LLMOPS_OPS_PAYLOAD_ROOT": "/var/lib/payloads", "LLMOPS_PACK_STATUS_DIR": "/var/lib/llmops/pack-status",
    "LLMOPS_PROMPT_LIBRARY": "/srv/llmopsagent/pack/prompt-library", "LLMOPS_FAKE_ANSWERS": "/srv/llmopsagent/pack/fake-answers/task10-holdout.json",
    "LLMOPS_EVAL_USER": "packeval"}
LLMOPS_DB_MOUNT = "/srv/llmopsagent/env/llmops.env:/run/llmops/db.env:ro"


def test_llmops_environment_names_and_values():
    env = SERVICES["llmops"]["environment"]
    assert env == LLMOPS_ENV
    assert not any("${" in str(v) for v in env.values())   # 가짜 모드 등은 고정 값 — compose에 바꾸는 변수 없음(D-150 Q1)


def test_llmops_has_no_real_mode_switch():
    text = (DOCKER / "compose.yaml").read_text(encoding="utf-8")
    assert "LLMOPS_LLM_MODE: fake" in text and not re.search(r"LLMOPS_LLM_MODE:\s*(real|\$)", text)
    assert not re.search(r"LLMOPS_(LLM|ANTHROPIC|GEMINI)_BASE_URL", text)   # 실제 provider 주소 자리도 두지 않음
    assert not any(k.startswith("LLMOPS_PACK_CI_") for k in SERVICES["llmops"]["environment"])   # 외부 실행 요청은 V-21 합친 뒤 · 승인 때


def test_llmops_dsn_file_is_mounted_read_only_not_env_file():
    svc = SERVICES["llmops"]
    assert LLMOPS_DB_MOUNT in svc["volumes"] and svc["environment"]["LLMOPS_DB_ENV_FILE"] == LLMOPS_DB_MOUNT.split(":")[1]
    for name, other in SERVICES.items():
        assert "/srv/llmopsagent/env/llmops.env" not in (other.get("env_file") or [])   # DSN이 환경 변수(Agent 쪽)로 가지 않음
        if name != "llmops":
            assert not any("llmops.env" in str(v) or "llmops-keys.env" in str(v) for v in other.get("volumes") or [])


def test_llmops_mounts_payloads_and_status_read_only_and_secrets_read_only():
    vols = SERVICES["llmops"]["volumes"]
    assert "payloads:/var/lib/payloads:ro" in vols and "status:/var/lib/status:ro" in vols      # V-10
    assert "llmops-data:/var/lib/llmops" in vols                                                # 자기 데이터만 쓰기
    assert all(v.endswith(":ro") for v in vols if v.startswith("/"))                            # 호스트 파일 · 폴더는 모두 읽기만
    assert "/srv/llmopsagent/keys/pack_ci_token:/run/llmops/pack_ci_token:ro" in vols
    assert "/srv/llmopsagent/pack:/srv/llmopsagent/pack:ro" in vols


def test_llmops_builds_from_the_llmops_tag_checkout():   # 답장 033-01: 이름 · 자리는 packrt 보고 020c 표 그대로
    assert not (DOCKER / "llmops.Dockerfile").exists()   # 이 저장소의 옛 초안은 지움 — 같은 이름이 남으면 VPS 날 헷갈림(답장 035-01)
    b = SERVICES["llmops"]["build"]
    assert b["context"] == "/srv/llmopsagent/llmops/docker" and b["dockerfile"] == "llmops.Dockerfile"
    assert b["args"]["LLMOPS_TAG"] == "${LLMOPS_TAG:-}" and b["args"]["LLMOPS_COMMIT"] == "${LLMOPS_COMMIT:-}"
    assert b["args"]["BASE_IMAGE"].startswith("python:3.14.2-slim-bookworm") and b["args"]["UV_IMAGE"].startswith("ghcr.io/astral-sh/uv:0.12.17")
    assert sorted(b["secrets"]) == ["llmops_read_key", "packrt_read_key"]
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    assert b["args"]["BASE_IMAGE"].split("@")[0] in d   # deploy 이미지와 같은 판(Pack venv가 같은 Python으로 돎 — 보고 034)


BASE_DIGEST = "sha256:e87711ef5c86aaeaa7031718a69db79d334d94c545c709583f651b8185870941"   # P-4(지시 033): python:3.14.2-slim-bookworm OCI index
UV_DIGEST = "sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc"     # P-4: ghcr.io/astral-sh/uv:0.12.17 OCI index


def test_base_and_uv_images_are_pinned_by_index_digest():   # P-4: 태그만으로 받지 않음 — deploy 이미지와 llmops 이미지가 같은 기본 이미지
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    assert f"ARG BASE_IMAGE=python:3.14.2-slim-bookworm@{BASE_DIGEST}\n" in d
    args = SERVICES["llmops"]["build"]["args"]
    assert args["BASE_IMAGE"] == f"python:3.14.2-slim-bookworm@{BASE_DIGEST}" and args["UV_IMAGE"] == f"ghcr.io/astral-sh/uv:0.12.17@{UV_DIGEST}"
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", x) for x in (BASE_DIGEST, UV_DIGEST))


def test_llmops_runs_as_root_with_three_caps_only():
    svc = SERVICES["llmops"]   # vps-prep Q-E (a) · Q-F (가): DAC_OVERRIDE 없음 → 쓰기는 파일 권한대로, 하위 프로세스는 packeval로 낮춤
    assert svc["user"] == "0:0" and svc["cap_drop"] == ["ALL"]
    assert sorted(svc["cap_add"]) == ["DAC_READ_SEARCH", "SETGID", "SETUID"]
    assert all("cap_add" not in other and other.get("user") is None for n, other in SERVICES.items() if n != "llmops")   # 다른 서비스는 그대로


def test_no_service_but_llmops_mounts_llmops_data():   # V-11: 장부가 PG로 간 뒤 배포단은 llmops 데이터를 붙이지 않음(ES-E · D-146)
    for name, svc in SERVICES.items():
        if name != "llmops":
            assert not any(str(v).split(":")[0] == "llmops-data" for v in svc.get("volumes") or [])


def test_llmops_key_and_dsn_examples_are_separate():
    names = lambda f: {l.partition("=")[0] for l in (DOCKER / f).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
    keys, dsn = names("env/llmops-keys.env.example"), names("env/llmops.env.example")
    assert keys == {"LLMOPS_OPENAI_API_KEY", "LLMOPS_ANTHROPIC_API_KEY", "LLMOPS_GEMINI_API_KEY"}
    assert dsn == {"LLMOPS_APP_DSN", "LLMOPSAGENT_RUNTIME_DSN", "LLMOPSAGENT_TRACE_DSN"} and not keys & dsn


def test_only_one_deploy_runs_by_default():
    running = [n for n in DEPLOYS if not SERVICES[n].get("profiles")]
    assert running == ["deploy-task10-v1-pg-local"]           # 운영 하나만 상주(D-84)


@pytest.mark.parametrize("path", ["compose.yaml", "deploy.Dockerfile",
                                  "env/postgres.env.example", "env/deploy.env.example", "env/llmops.env.example", "env/backup.env.example",
                                  "env/deploy-tools.env.example", "env/llmops-keys.env.example"])
def test_no_secret_values_in_files(path):
    text = (DOCKER / path).read_text(encoding="utf-8")
    assert not SECRET_LIKE.search(text)
    assert "COPY .env" not in text and "ADD .env" not in text


@pytest.mark.parametrize("path", ["env/postgres.env.example", "env/deploy.env.example", "env/llmops.env.example", "env/backup.env.example",
                                  "env/deploy-tools.env.example", "env/llmops-keys.env.example"])
def test_env_examples_have_names_only(path):
    for line in (DOCKER / path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, sep, value = line.partition("=")
            assert sep and re.fullmatch(r"[A-Z][A-Z0-9_]+", name) and value == ""


def test_dockerfiles_use_build_secrets_and_non_root():
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    assert "--mount=type=secret,id=deploy_read_key" in d and "--mount=type=secret,id=packrt_read_key" in d
    assert re.search(r"^USER deploy$", d, re.M)
    assert "python:3.14.2" in d   # pinned.json 판


def test_tag_variables_do_not_block_non_build_commands():
    text = (DOCKER / "compose.yaml").read_text(encoding="utf-8")
    assert ":?" not in text                                                 # stop · exec · ps에도 걸리지 않게(답장 019-02)
    for f, tag in (("deploy.Dockerfile", "DEPLOY_TAG"),):   # llmops 이미지는 llmops 저장소 Dockerfile(옛 초안 지움 — 답장 035-01)
        d = (DOCKER / f).read_text(encoding="utf-8")
        assert f'test -n "${tag}" && test -n "$PACKRT_TAG"' in d          # 빈 태그는 빌드 첫 단계에서 멈춤


@pytest.mark.parametrize("f", ["deploy.Dockerfile"])   # 옛 llmops 초안 갈래 지움(답장 035-01)
def test_base_image_and_known_hosts_slots(f):
    d = (DOCKER / f).read_text(encoding="utf-8")
    assert "ARG BASE_IMAGE=python:3.14.2-slim-bookworm" in d and d.count("FROM ${BASE_IMAGE}") == 2
    assert "ssh-keyscan" not in d and "COPY github_known_hosts /root/.ssh/known_hosts" in d


GITHUB_FP = {"ssh-rsa": "uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s", "ecdsa-sha2-nistp256": "p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
             "ssh-ed25519": "+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"}   # GitHub 문서 공개 지문(SHA256)


def test_github_known_hosts_match_published_fingerprints():   # 답장 030-01 P-3: pack-10 ci/runner/github_known_hosts와 같은 키 줄
    import base64
    import hashlib
    kh = (DOCKER / "github_known_hosts").read_text(encoding="utf-8")
    keys = [l.split() for l in kh.splitlines() if l.strip() and not l.startswith("#")]
    assert len(keys) == 3 and all(k[0] == "github.com" and len(k) == 3 for k in keys)
    got = {k[1]: base64.b64encode(hashlib.sha256(base64.b64decode(k[2])).digest()).decode().rstrip("=") for k in keys}
    assert got == GITHUB_FP


def test_github_known_hosts_same_key_lines_as_pack10_runner():
    pack = ROOT.parent / "task10-pack" / "ci" / "runner" / "github_known_hosts"
    if not pack.exists():
        pytest.skip("task10-pack 저장소가 옆에 없음")
    lines = lambda t: sorted(l for l in t.splitlines() if l.strip() and not l.startswith("#"))
    assert lines((DOCKER / "github_known_hosts").read_text(encoding="utf-8")) == lines(pack.read_text(encoding="utf-8"))


def test_every_service_opts_out_of_traefik():   # 답장 030-01: 같은 VPS Traefik(exposedByDefault)이 router를 만들지 않게
    assert all(svc.get("labels") == {"traefik.enable": "false"} for svc in SERVICES.values())


def test_deploy_image_points_scripts_py_at_the_image_venv():   # 보고 030 P-5a: es3_check → scripts/py가 /app/.venv를 찾아 늘 종료 2이던 것
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    final = d[d.rindex("FROM ${BASE_IMAGE}"):]
    assert "COPY --from=build /opt/venv /opt/venv" in final and re.search(r"^ENV .*\bDEPLOY_VENV=/opt/venv\b", final, re.M)
    assert '"$(dirname "$0")/.."' in (ROOT / "scripts" / "py").read_text()   # scripts/py는 저장소 뿌리(/app)에서 돎


def test_scripts_py_uses_deploy_venv_or_stops(tmp_path):
    import subprocess
    import sys
    venv = Path(sys.prefix)   # 시험을 돌리는 venv를 DEPLOY_VENV로
    r = subprocess.run([str(ROOT / "scripts" / "py"), "-c", "import sys; print(sys.prefix)"], env={"DEPLOY_VENV": str(venv), "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0 and Path(r.stdout.strip()).resolve() == venv.resolve()
    r = subprocess.run([str(ROOT / "scripts" / "py"), "-c", "pass"], env={"DEPLOY_VENV": str(tmp_path / "none"), "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 2 and "가상환경이 없습니다" in r.stderr


def test_deploy_image_runs_pip_check_and_leaves_out_test_packages():   # 답장 030-01
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    build = d[:d.rindex("FROM ${BASE_IMAGE}")]
    assert "RUN /opt/venv/bin/pip check" in build and build.index("pip check") > build.index("build_commit_check.py")
    assert "| grep -Ev '^(pytest|pluggy|iniconfig|pygments)==' > /tmp/deploy-req.txt" in build
    req = (ROOT / "requirements" / "deploy-venv.txt").read_text(encoding="utf-8")
    assert all(re.search(rf"^{n}==", req, re.M) for n in ("pytest", "pluggy", "iniconfig", "pygments"))   # 목록에 있어서 빼는 것이 뜻 있음


def test_real_env_files_under_docker_env_are_ignored():
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-q", "docker/env/deploy.env"], cwd=ROOT)
    assert r.returncode == 0


# ---------------------------------------------------------------- V-40(a) 실행 자리 · 운영 설정(ES-G0)
TOOLS = SERVICES["deploy-tools"]
KEY2 = "/srv/llmopsagent/keys/pack_read:/run/keys/pack_read:ro"
KEY7 = "/srv/llmopsagent/keys/c1a15_pack_read:/run/keys/c1a15_pack_read:ro"   # V-4(D-366)
LIVE = "/srv/llmopsagent/deploy-config:/app/config-live"
TOOLS_LEDGER_ENV = "/srv/llmopsagent/env/deploy-tools.env:/run/deploy/deploy.env:ro"


def test_deploy_tools_is_one_shot_without_ports():
    assert TOOLS.get("profiles") == ["tools"] and "ports" not in TOOLS and "restart" not in TOOLS
    assert TOOLS["build"] == SERVICES["deploy-task10-v1-pg-local"]["build"]          # 서버와 같은 이미지(판이 같음)


def test_key2_and_live_config_write_only_in_deploy_tools():
    for name, svc in SERVICES.items():
        vols = [str(v) for v in svc.get("volumes") or []]
        assert (KEY2 in vols) == (name == "deploy-tools"), name                     # 열쇠 ②는 deploy-tools에만
        assert (KEY7 in vols) == (name == "deploy-tools"), name                     # 열쇠 ⑦도 deploy-tools에만(V-4)
        assert (LIVE in vols) == (name == "deploy-tools"), name                     # 운영 설정 쓰기는 deploy-tools에만
    for n in DEPLOYS:
        assert LIVE + ":ro" in SERVICES[n]["volumes"]
    assert "pack_read" in TOOLS["environment"]["GIT_SSH_COMMAND"] and "StrictHostKeyChecking=yes" in TOOLS["environment"]["GIT_SSH_COMMAND"]
    # 비밀은 열쇠 ② + 읽기 전용 장부 DSN 하나만(D-146 — V-40(a) 약속 변경 '배포단 비밀 없음' → 'runtime_reader DSN 하나만'). 허용 목록:
    assert "env_file" not in TOOLS and "secrets" not in TOOLS                                    # env 파일 · 실행 중 비밀(/run/secrets) 없음
    assert set(TOOLS["environment"]) == {"GIT_SSH_COMMAND"} and "${" not in str(TOOLS["environment"])
    vols = TOOLS["volumes"]
    assert all(isinstance(v, str) for v in vols)                                                # long 문법 bind 없음(아래 대조를 비켜 가지 않게)
    binds = sorted(v for v in vols if v.startswith("/"))
    assert binds == sorted([LIVE, TOOLS_LEDGER_ENV, KEY2, KEY7, C1A15_RELEASES_RO])             # 호스트 bind는 이 다섯뿐(폴더째 env · keys · /srv mount 없음)
    # D-366 V-4 (가) 약속 변경: '넷' → '다섯' — 다섯째는 c1a15-pack 읽기 열쇠 ⑦ 파일 하나, 읽기만(fetch가 Pack 캐시 이름으로 고름)
    # D-334 덧 Q-7 (가) 약속 변경: V-40 '셋' → '넷' — 넷째는 비밀 아닌 C1-a15 벡터 DB 사본 폴더 하나, 읽기만(register 묶기 대조 · R12)
    assert {v.split(":")[0] for v in vols if not v.startswith("/")} == {"pack-cache-deploy", "status"}   # named volume도 이 둘뿐(pgdata · llmops-data 없음)
    names = [l.partition("=")[0] for l in (DOCKER / "env" / "deploy-tools.env.example").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert names == ["LLMOPSAGENT_RUNTIME_DSN"]                                       # trace_writer · owner 등 다른 이름이 들어가면 실패
    assert not any("llmops-ledger" in str(v) or "llmops-data" in str(v) for v in TOOLS["volumes"])   # SQLite 장부 mount 없음(ES-E)


def test_deploy_tools_ledger_env_matches_live_config_slot():
    from deploy_server.settings import load_settings
    for name in ("v1-pg-vps", "v0-pg-vps"):
        s = load_settings(ROOT / "config" / f"{name}.yaml")
        assert s.registration == {"mode": "llmops_pg"} and Path(s.runtime_db["env_file"]) == Path("/run/deploy/deploy.env").resolve()


def test_no_docker_socket_anywhere():
    text = (DOCKER / "compose.yaml").read_text(encoding="utf-8")
    assert "docker.sock" not in text


def test_deploy_image_has_known_hosts_slot_for_tools():
    d = (DOCKER / "deploy.Dockerfile").read_text(encoding="utf-8")
    final = d[d.rindex("FROM ${BASE_IMAGE}"):]
    assert "COPY github_known_hosts /etc/ssh/ssh_known_hosts" in final



@pytest.mark.parametrize("f", ["deploy.Dockerfile"])   # 옛 llmops 초안 갈래 지움(답장 035-01)
def test_dockerfile_no_trailing_comments_outside_run(f):
    """독립 확인 F-1 · F-9: RUN 밖 지시 줄(이어진 줄 포함)에는 줄 끝 # 주석이 없어야 함 — Docker는 그 #을 인자로 읽음."""
    instr = None
    for line in (DOCKER / f).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if instr is None:
            instr = s.split()[0].upper()
        if instr != "RUN":
            assert "#" not in s, line
        instr = instr if s.endswith("\\") else None


def test_v2_v4_configs_differ_from_v1_only_where_planned():
    """지시 042(task10-pack 넘김 H-6 ~ H-9): v2 · v4 VPS 설정은 v1-pg-vps와 같은 실행 설정 · 경로 · 장부 · 기록이고,
    다른 칸은 배포 id · 태그 · (v4) 모델 연결 · ML 'NONE' 끝 node · grounding prediction뿐. expect_revision은 register가 운영 설정에만 채움."""
    from deploy_server.settings import load_settings
    v1 = load_settings(ROOT / "config" / "v1-pg-vps.yaml")
    keep = ("retention_class", "pack_repo", "pack_subdir", "releases_subdir", "runtime_db", "data_dir", "review_policy", "provider",
            "registration", "trace", "secret_env_names")
    for tag in ("v2", "v4"):
        s = load_settings(ROOT / "config" / f"{tag}-pg-vps.yaml")
        assert (s.deployment_id, s.pack_ref, s.expect_revision) == (f"task10@{tag}-pg-local", tag, None)
        assert all(getattr(s, k) == getattr(v1, k) for k in keep), [k for k in keep if getattr(s, k) != getattr(v1, k)]
        assert {k: v for k, v in s.execution.items() if k != "models"} == {k: v for k, v in v1.execution.items() if k != "models"}
    v2, v4 = (load_settings(ROOT / "config" / f"{t}-pg-vps.yaml") for t in ("v2", "v4"))
    assert v2.execution["models"] == {} and (v2.outcome_by_node, v2.grounding_keys) == (v1.outcome_by_node, v1.grounding_keys)
    assert v4.execution["models"] == {"failure_classifier": {"release": "mr-ai4i-standin-v3"}}
    assert v4.outcome_by_node == {**v1.outcome_by_node, "record_no_issue_ml": "no_issue"}
    assert v4.grounding_keys == [*v1.grounding_keys, "prediction"]


def test_v2_v4_services_are_standby_with_own_port_and_data():
    ports = {n: SERVICES[n]["ports"][0] for n in DEPLOYS}
    assert len(set(ports.values())) == len(ports)                                          # 서로 다른 포트(switch는 새 서비스를 먼저 띄움)
    for tag, port in (("v2", 8784), ("v4", 8785)):
        svc = SERVICES[f"deploy-task10-{tag}-pg-local"]
        assert svc["profiles"] == ["standby"] and svc["environment"] == {"DEPLOY_CONFIG": f"config-live/{tag}-pg-vps.yaml", "DEPLOY_PORT": str(port)}
        assert svc["ports"] == [f"127.0.0.1:{port}:{port}"] and f"deploy-data-task10-{tag}-pg-local:/var/lib/deploy/data" in svc["volumes"]
        assert svc["volumes"][1:] == SERVICES["deploy-task10-v1-pg-local"]["volumes"][1:]   # 데이터 폴더 말고는 v1과 같은 mount


def test_r12_overlay_adds_three_mounts_to_deploy_tools_only():
    """D-325 W-2: R12 덧파일은 deploy-tools에만 data volume · 받은 편지(읽기만) · 적재 비밀(읽기만)을 더함 — 서버 서비스 · 다른 비밀은 0."""
    doc = yaml.safe_load((ROOT / "docker" / "compose.r12.yaml").read_text(encoding="utf-8"))
    assert set(doc) == {"services"} and set(doc["services"]) == {"deploy-tools"}
    vols = doc["services"]["deploy-tools"]["volumes"]
    assert vols == ["${R12_DATA_VOLUME:?R12_DATA_VOLUME}:/var/lib/deploy/data",
                    "${R12_INBOX_DIR:?R12_INBOX_DIR}:/var/lib/deploy/data/r12-inbox:ro",
                    "${R12_LOADER_ENV:-/dev/null}:/run/deploy/index-loader.env:ro"]
    base = yaml.safe_load((ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8"))
    assert not [n for n, s in base["services"].items() if any("index-loader" in str(v) for v in s.get("volumes") or [])]   # 서버 · 기본 tools에는 적재 비밀 0



# ------------------------------------------------------------------ C1-a15 VPS 단계 A(D-334 V-1 · V-2)
def test_c1a15_service_is_the_only_one_on_the_edge_network():
    """D-330 DQ-1 · D-333 A-1 · A-2: 전용 망 c1a15-edge는 deploy가 만들고 이름 고정 · C1-a15 서비스만 붙음(공유 PG · llmops 망과 앱을 떼어 둠)."""
    assert COMPOSE["networks"]["c1a15-edge"] == {"name": "${C1A15_EDGE_NETWORK:-c1a15-edge}"}
    on_edge = [n for n, svc in SERVICES.items() if "c1a15-edge" in (svc.get("networks") or [])]
    assert on_edge == [C1A15] and SERVICES[C1A15]["networks"] == ["internal", "c1a15-edge"]
    svc = SERVICES[C1A15]
    assert svc["environment"] == {"DEPLOY_CONFIG": "config-live/c1a15-v1-pg-vps.yaml", "DEPLOY_PORT": "8786", "ORT_DISABLE_TELEMETRY": "1"}
    assert svc["ports"] == ["127.0.0.1:8786:8786"] and svc["profiles"] == ["standby"] and svc["labels"] == {"traefik.enable": "false"}
    assert "/srv/llmopsagent/c1a15-releases:/srv/llmopsagent/c1a15-releases:ro" in svc["volumes"]
    assert [n for n in SERVICES if any("c1a15-releases" in str(v) for v in SERVICES[n].get("volumes") or [])] == [C1A15, "deploy-tools"]
    assert C1A15_RELEASES_RO in SERVICES["deploy-tools"]["volumes"]   # 읽기만(D-334 덧 Q-7 (가))


def test_c1a15_vps_config_carries_the_pack_v6_execution_config():
    """규칙 06: 실행 설정은 c1a15-pack 태그 v6 pack/execution/deploy.yaml을 그대로 옮김(entrypoint · models · stores) · R12 쓰기 폴더가 extra 첫 칸."""
    import subprocess
    from deploy_server.settings import load_settings
    pack = ROOT.parent / "c1a15-pack"
    out = subprocess.run(["git", "-C", str(pack), "show", "v6:pack/execution/deploy.yaml"], capture_output=True, text=True)
    if out.returncode:
        pytest.skip("c1a15-pack 태그 v6가 없습니다")
    want = yaml.safe_load(out.stdout)
    s = load_settings(ROOT / "config" / "c1a15-v1-pg-vps.yaml")
    assert (s.deployment_id, s.pack_ref, s.retention_class, s.provider) == ("c1a15@v1-pg-local", "v6", "standard", "fake")
    assert s.execution == {"entrypoint": want["entrypoint"], "stores": want["stores"], "models": want["models"]}
    assert set(s.execution["stores"]) == {"history", "manuals"}   # v5부터 manuals 필수
    assert [str(p) for p in s.release_roots_extra][1] == "/srv/llmopsagent/c1a15-releases"
    assert Path(s.release_roots_extra[0]) == (Path(s.data_dir) / "releases")
    assert s.registration == {"mode": "llmops_pg"} and s.expect_revision is None   # expect_revision은 register가 운영 설정에만



def test_c1a15_stage_b_template_is_stage_a_with_pack_v6_pg_stores():
    """D-375: 단계 B 템플릿 = 단계 A 운영 설정과 store 줄만 다르고, store · models · entrypoint는 c1a15-pack v6 deploy-pg.yaml 그대로 —
    같은 release id라 drs가 같아 같은 장부 줄(reconfigure가 /info drs로 다시 봄)."""
    import subprocess
    from deploy_server.settings import load_settings
    pack = ROOT.parent / "c1a15-pack"
    out = subprocess.run(["git", "-C", str(pack), "show", "v6:pack/execution/deploy-pg.yaml"], capture_output=True, text=True)
    if out.returncode:
        pytest.skip("c1a15-pack 태그 v6가 없습니다")
    want = yaml.safe_load(out.stdout)
    a, b = load_settings(ROOT / "config" / "c1a15-v1-pg-vps.yaml"), load_settings(ROOT / "config" / "c1a15-v1-pgvector-vps.yaml")
    assert b.execution == {"entrypoint": want["entrypoint"], "stores": want["stores"], "models": want["models"]}
    assert {k: v["release"] for k, v in b.execution["stores"].items()} == {k: v["release"] for k, v in a.execution["stores"].items()}
    assert all(v["backend"] == "pgvector" and v["connection"] == {"env": "LLMOPSAGENT_RUNTIME_DSN"} for v in b.execution["stores"].values())
    for f in ("deployment_id", "pack_repo", "pack_ref", "expect_revision", "runtime_db", "registration", "trace", "data_dir", "release_roots_extra",
              "provider", "review_policy", "retention_class"):
        assert getattr(a, f) == getattr(b, f), f
    da = yaml.safe_load((ROOT / "config" / "c1a15-v1-pg-vps.yaml").read_text())
    db = yaml.safe_load((ROOT / "config" / "c1a15-v1-pgvector-vps.yaml").read_text())
    da["execution"].pop("stores"), db["execution"].pop("stores")
    assert da == db                                                                        # store 줄 밖 바이트 뜻 같음


C1A15_KEY = "/srv/llmopsagent/keys/c1a15-openai.env:/run/keys/c1a15-openai.env:ro"


def test_c1a15_query_embedding_key_only_on_the_c1a15_service():
    """D-416 ③ (나): 질의 임베딩 키 파일은 C1-a15 배포 서비스에만 읽기 전용 — deploy-tools · 다른 서비스 0(V-40 그대로)."""
    on = [n for n, svc in SERVICES.items() if C1A15_KEY in [str(v) for v in svc.get("volumes") or []]]
    assert on == [C1A15]
    assert not any("c1a15-openai" in str(v) for v in TOOLS["volumes"])


def test_c1a15_v7_real_template_is_v6_template_with_pack_v7_deploy_real():
    """D-416 ③ (나): v7 템플릿 = v6 운영 템플릿에서 pack.ref · stores(c1a15-pack v7 deploy-real.yaml 그대로) · embedding 칸만 다름(되돌림 = v6 템플릿)."""
    import subprocess
    from deploy_server.settings import load_settings
    pack = ROOT.parent / "c1a15-pack"
    out = subprocess.run(["git", "-C", str(pack), "show", "v7:pack/execution/deploy-real.yaml"], capture_output=True, text=True)
    if out.returncode:
        pytest.skip("c1a15-pack 태그 v7이 없습니다")
    want = yaml.safe_load(out.stdout)
    s = load_settings(ROOT / "config" / "c1a15-v7-real-vps.yaml")   # 키 파일 없이도 읽힘(파일 있음은 서버 시작 때)
    assert (s.deployment_id, s.pack_ref, s.provider) == ("c1a15@v1-pg-local", "v7", "fake")
    assert s.execution == {"entrypoint": want["entrypoint"], "stores": want["stores"], "models": want["models"]}
    assert s.embedding == {"provider": "http", "env_file": Path("/run/keys/c1a15-openai.env"), "base_url": "https://api.openai.com/v1"}
    a = yaml.safe_load((ROOT / "config" / "c1a15-v1-pg-vps.yaml").read_text())
    b = yaml.safe_load((ROOT / "config" / "c1a15-v7-real-vps.yaml").read_text())
    for d in (a, b):
        d["pack"].pop("ref"), d["execution"].pop("stores"), d.pop("embedding", None)
    assert a == b

