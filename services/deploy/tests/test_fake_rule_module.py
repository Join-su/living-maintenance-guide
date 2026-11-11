"""가짜 provider = 배포 트리의 Pack 규칙 모듈(VFE EV-005 ES-F, CHG-06 · V-37, 오케 답장 019-05).
- 모듈이 없는 트리(태그 v0 · v1) = 옛 규칙 그대로(바이트 보존은 run 폴더 verification/v37_capture.py 기준값 대조)
- 모듈이 있는 트리(task10-pack dev 55d08b3) = 그 트리의 tools/fake/rule.py. 예 ①(V2 구조 사본 polish_answer → passthrough 10 case) · 예 ②(draft 칸 제목을 바꿈 → 거부)
- 시험 사본: task10-pack을 시험 폴더에 clone(원본은 읽기만)해 태그를 만든다. 실제 provider · DB · 네트워크 없음."""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT, TASK10

RULE_COMMIT = "55d08b359806f41456e13984c5643289d2d762bd"     # task10-pack EV-022 — tools/fake/rule.py 들어온 commit(10-19 이력 정리 뒤 번호)
FILE_EXECUTION = {"entrypoint": "respond", "models": {},
                  "stores": {"history": {"backend": "sqlite-file", "release": "hc-ai4i2020-s7-v2"},
                             "manuals": {"backend": "flat-local", "release": "ir-manuals-v2"}}}
LIB_FILE = "prompts/lib/polish_answer.md"
POLISH_OPS = [   # task10-pack tests/test_apply_approved.py STRUCT_OPS(create_file은 packrt 0.8.0에 없어 파일을 먼저 둠) + test_fake_rule.py ESC_EDGES
    {"op": "add_node", "graph": "respond", "after": "draft", "node": {
        "id": "polish_answer", "kind": "llm@1", "reads": ["event", "alarm_resolution", "evidence", "answer"], "writes": ["answer"],
        "params": {"provider": "openai", "model": "gpt-4.1-mini", "prompt": "prompts/draft.md", "output": "GroundedResponse", "temperature": 0}}},
    {"op": "remove_edge", "graph": "respond", "edge": ["draft", "answer_check"]},
    {"op": "add_edge", "graph": "respond", "edge": ["draft", "polish_answer"]},
    {"op": "add_edge", "graph": "respond", "edge": ["polish_answer", "answer_check"]},
    {"op": "set", "graph": "respond", "node": "polish_answer", "param": "prompt", "value": LIB_FILE},
    {"op": "add_edge", "graph": "respond", "edge": ["escalation_draft", "polish_answer"]},
    {"op": "remove_edge", "graph": "respond", "edge": ["escalation_draft", "answer_check"]},
]
DRAFT_HEADING = "## 근거(검색 결과, 거리 가까운 순)"
ESC_HEADING = "## 참고 근거(검색 결과)"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def build_rule_repo(dest):
    """task10-pack을 dest에 clone하고 시험 태그 셋을 만든다: t-dev(55d08b3 그대로) · t-v2(polish_answer 구조) · t-rn(draft 칸 제목 바꿈)."""
    from packrt.ir.patch import apply_patch
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(TASK10), str(dest)], check=True)
    _git(dest, "checkout", "-q", "-b", "es-f-test", RULE_COMMIT)
    _git(dest, "tag", "t-dev")
    lib = _git(dest, "show", f"{RULE_COMMIT}:prompt-library/polish_answer.md")
    (dest / "pack" / LIB_FILE).parent.mkdir(parents=True, exist_ok=True)
    (dest / "pack" / LIB_FILE).write_text(lib + "\n", encoding="utf-8")
    res = apply_patch(dest / "pack", POLISH_OPS)
    assert res.ok, res.errors
    for rel, data in res.files.items():                                      # apply_patch는 바뀐 파일을 돌려주기만 함
        (dest / "pack" / rel).write_bytes(data)
    _git(dest, "add", "-A", "pack")
    _git(dest, "commit", "-q", "-m", "test: V2 polish_answer")
    _git(dest, "tag", "t-v2")
    _git(dest, "checkout", "-q", "-b", "es-f-rn", "t-dev")
    draft = dest / "pack" / "prompts" / "draft.md"
    text = draft.read_text(encoding="utf-8")
    assert text.count(DRAFT_HEADING) == 1
    draft.write_text(text.replace(DRAFT_HEADING, "## 근거(가까운 순)"), encoding="utf-8")
    _git(dest, "commit", "-q", "-am", "test: renamed draft section")
    _git(dest, "tag", "t-rn")
    _git(dest, "checkout", "-q", "-b", "es-f-rne", "t-dev")
    esc = dest / "pack" / "prompts" / "escalation.md"
    text = esc.read_text(encoding="utf-8")
    assert text.count(ESC_HEADING) == 1
    esc.write_text(text.replace(ESC_HEADING, "## 참고 근거(목록)"), encoding="utf-8")
    _git(dest, "commit", "-q", "-am", "test: renamed escalation section")
    _git(dest, "tag", "t-rne")
    return dest


def deploy(repo, ref, tmp, packs_dir):
    from deploy_server.app import Deployment
    from deploy_server.settings import load_settings
    from fake_writer import FakeWriter
    s = load_settings(ROOT / "config" / "v1-pg-local.yaml",
                      overrides={"data_dir": str(tmp / f"data-{ref}"), "packs_dir": str(packs_dir), "execution": FILE_EXECUTION,
                                 "pack": {"repo": str(repo), "ref": ref, "pack_subdir": "pack"},
                                 "registration": {"mode": "none"}})   # 장부(저장소 .env의 PG DSN)는 읽지 않음 — A5 뒤 v1-pg-local = llmops_pg
    return Deployment(s, trace_writer=FakeWriter(s.data_dir / "trace", secrets=lambda: []))


def run_all(dep):
    """holdout case마다 POST /runs, 가짜 provider chat 출력 · 한계를 case별로 모음."""
    from deploy_server.app import create_app
    prov, rec = dep.provider, []
    real = prov.chat

    def chat(*a, **kw):
        n = len(prov.limits)
        try:
            text, usage = real(*a, **kw)
        except Exception as e:
            rec.append({"error": str(e), "limits": prov.limits[-1] if len(prov.limits) > n else None})
            raise
        rec.append({"text": text, "limits": prov.limits[-1]})
        return text, usage
    prov.chat = chat
    rows = [json.loads(l) for l in (dep.copy.pack_dir / "evals/task10-holdout/dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    client = TestClient(create_app(deployment=dep), raise_server_exceptions=False)
    out = {}
    for r in rows:
        start = len(rec)
        resp = client.post("/runs", content=json.dumps(r["input"], ensure_ascii=False).encode(),
                           headers={"Content-Type": "application/json", "Idempotency-Key": f"esf-{r['case_id']}"})
        out[r["case_id"]] = {"code": resp.status_code, "body": resp.json(), "chat": rec[start:]}
    return out


@pytest.fixture(scope="module")
def rule_repo(tmp_path_factory):
    if subprocess.run(["git", "-C", str(TASK10), "cat-file", "-e", f"{RULE_COMMIT}^{{commit}}"]).returncode:
        pytest.skip("task10-pack에 55d08b3이 없음")
    tags_before = subprocess.run(["git", "-C", str(TASK10), "tag", "-l", "t-*"], capture_output=True, text=True).stdout
    repo = build_rule_repo(tmp_path_factory.mktemp("rule") / "task10-pack")
    yield repo
    # 원본 task10-pack은 읽기만: 시험 태그 · 규칙 모듈 바이트코드가 원본에 생기지 않음(다른 세션이 같은 저장소에서 일하므로 상태 전체는 비교하지 않음)
    assert subprocess.run(["git", "-C", str(TASK10), "tag", "-l", "t-*"], capture_output=True, text=True).stdout == tags_before   # 시험 태그는 사본에만
    assert not (TASK10 / "tools" / "fake" / "__pycache__").exists() or not any(
        p.stat().st_mtime > repo.stat().st_mtime for p in (TASK10 / "tools" / "fake" / "__pycache__").iterdir())


@pytest.fixture(scope="module")
def runs(rule_repo, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("runs")
    packs = tmp / "packs"
    out = {}
    for ref, repo in (("v1", TASK10), ("t-dev", rule_repo), ("t-v2", rule_repo), ("t-rne", rule_repo)):
        dep = deploy(repo, ref, tmp, packs)
        out[ref] = {"rule": dep.provider.rule_name, "limits": dep.provider.limits, "cases": run_all(dep), "packs": packs, "commit": dep.copy.commit}
        dep.close()
    return out


def test_trees_without_module_keep_old_rule():
    from deploy_server.fake_provider import OLD_RULE, load_rule
    for tag in ("v0", "v1"):
        commit = subprocess.run(["git", "-C", str(TASK10), "rev-parse", f"{tag}^{{commit}}"], capture_output=True, text=True).stdout.strip()
        assert len(commit) == 40, tag                                         # 태그를 못 찾으면 통과로 두지 않음
        assert load_rule(TASK10, commit, ROOT / "var" / "never-used") is None, tag   # 모듈 없음 → 아무것도 꺼내지 않음
    assert not (ROOT / "var" / "never-used").exists()


def test_old_rule_run_is_marked(runs):
    assert runs["v1"]["rule"] == "가짜 provider 한계(옛 규칙)" and all(x == [] for x in runs["v1"]["limits"])


def test_module_tree_uses_its_own_rule_and_matches_v1_answers(runs):
    """dev 트리(Pack = v1)는 그 트리의 모듈을 부르고, V1 답과 바이트까지 같음 · 한계 없음(등록 프롬프트)."""
    dev, v1 = runs["t-dev"], runs["v1"]
    assert dev["rule"] == "task10-pack/fake-rule@1"
    assert {c: [x["text"] for x in v["chat"]] for c, v in dev["cases"].items()} == {c: [x["text"] for x in v["chat"]] for c, v in v1["cases"].items()}
    assert all(x["limits"] == [] for v in dev["cases"].values() for x in v["chat"])
    root = dev["packs"] / f"task10-pack-{dev['commit'][:12]}-fake-rule"
    assert (root / "tools/fake/rule.py").is_file() and (root / "fake-answers/known-prompts.json").is_file()
    assert not list(root.rglob("__pycache__")) and not ((root / "tools/fake/rule.py").stat().st_mode & 0o222)   # 읽기 전용, 바이트코드 없음


def test_example_1_polish_answer_passes_the_answer_through(runs):
    """예 ①: V2 구조 사본 — polish_answer를 지난 10 case는 앞 답 그대로 + passthrough."""
    v2 = runs["t-v2"]
    passed = sorted(c for c, v in v2["cases"].items() if any(x["limits"] == ["passthrough"] for x in v["chat"]))
    assert passed == ["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C12"]
    for c in passed:
        chat = v2["cases"][c]["chat"]
        assert len(chat) == 2 and json.loads(chat[1]["text"]) == json.loads(chat[0]["text"]) and chat[0]["limits"] == []   # 앞 답 그대로(키 차례만 packrt 렌더 차례)
        assert v2["cases"][c]["code"] == 201


def test_example_2_renamed_section_is_refused_and_the_run_fails(runs):
    """예 ②(실행 경로): 파일 backend에서 v1 case는 모두 escalation 경로라, 그 프롬프트의 칸 제목을 바꾼 사본으로 돌림 →
    chat이 있던 case 전부 거부 → 그 실행은 실패(조용히 빈 답을 내지 않음), 한계 = [prompt_unknown, refused]."""
    rne, v1 = runs["t-rne"], runs["v1"]
    used = sorted(c for c, v in v1["cases"].items() if v["chat"])
    assert used
    for c in used:
        v = rne["cases"][c]
        assert v["chat"][-1].get("limits") == ["prompt_unknown", "refused"] and "가짜 응답 한계(refused)" in v["chat"][-1]["error"], c
        assert v["code"] != 201 or v["body"].get("status") == "failed", c
    for c in sorted(set(v1["cases"]) - set(used)):                            # chat이 없는 case(C10 · C11)는 그대로
        assert rne["cases"][c]["code"] == v1["cases"][c]["code"] and not rne["cases"][c]["chat"]


def _render(pack_dir, prompt_file, state):
    from packrt.run.templates import render
    return render((pack_dir / prompt_file).read_text(encoding="utf-8"), state)


DRAFT_STATE = {"event": {"event_id": "E-1"}, "alarm_resolution": {"query_text": "q"},
               "evidence": [{"id": "H-1", "source_type": "history", "distance": 0.2, "failure_type": "TWF"}, {"id": "M-1", "source_type": "manual"}]}


def test_example_2_renamed_draft_section_is_refused_by_the_tree_rule(rule_repo, runs):
    """예 ②(draft 프롬프트): t-rn 트리의 draft.md를 packrt 렌더러로 채워 그 트리의 규칙 모듈에 넣음 → 거부 [prompt_unknown, refused].
    같은 상태로 원래 draft.md(t-dev)는 draft 답 · 한계 없음."""
    from deploy_server.fake_provider import FakeProvider, load_rule
    packs = runs["t-dev"]["packs"]
    for ref, refused in (("t-dev", False), ("t-rn", True)):
        commit = _git(rule_repo, "rev-parse", f"{ref}^{{commit}}")
        prov = FakeProvider(rule=load_rule(rule_repo, commit, packs))
        tree = packs / f"render-{ref}"
        if not tree.exists():
            tree.mkdir()
            subprocess.run(f"git -C {rule_repo} archive {commit} pack | tar -x -C {tree}", shell=True, check=True)
        prompt = _render(tree / "pack", "prompts/draft.md", DRAFT_STATE)
        if refused:
            with pytest.raises(prov.rule.FakeRefused):
                prov.chat("openai", "m", [{"role": "user", "content": prompt}])
            assert list(prov.limits) == [["prompt_unknown", "refused"]]
        else:
            text, _ = prov.chat("openai", "m", [{"role": "user", "content": prompt}])
            assert json.loads(text)["kind"] == "draft" and list(prov.limits) == [[]]


def test_fake_provider_without_rule_is_unchanged():
    from deploy_server.fake_provider import FakeProvider
    p = FakeProvider()
    assert p.rule is None and p.rule_name == "가짜 provider 한계(옛 규칙)"


def test_rule_runs_isolated_without_env_or_site_packages(tmp_path, monkeypatch):
    """규칙 모듈은 따로 띄운 파이썬(-I -S -B, 빈 환경)에서 돎: 환경 변수(DB 비밀 등) 없음, site-packages 없음(yaml · packrt import 불가)."""
    from deploy_server.fake_provider import TreeRule
    monkeypatch.setenv("LLMOPSAGENT_TRACE_DSN", "postgresql://u:secret@h/d")
    rule = tmp_path / "tools" / "fake" / "rule.py"
    rule.parent.mkdir(parents=True)
    rule.write_text("""import json, os, sys
FORMAT = "probe"
class FakeRefused(ValueError):
    limits = ["refused"]
def load_known():
    return ()
def respond_text(prompt):
    def can(name):
        try:
            __import__(name)
            return True
        except ImportError:
            return False
    return json.dumps({"env": sorted(os.environ), "site": [p for p in sys.path if "site-packages" in p], "yaml": can("yaml"), "packrt": can("packrt")}), []
""")
    text, limits = TreeRule(rule).respond_text("x")
    got = json.loads(text)
    assert [k for k in got["env"] if k not in ("__CF_USER_TEXT_ENCODING", "LC_CTYPE")] == [] and not got["site"]
    assert got["yaml"] is False and got["packrt"] is False and limits == []
    assert not (rule.parent / "__pycache__").exists()


def test_broken_known_list_stops_at_deploy_time(tmp_path):
    from deploy_server.fake_provider import TreeRule
    rule = tmp_path / "rule.py"
    rule.write_text('FORMAT = "x"\ndef load_known():\n    raise ValueError("목록 형식 틀림")\n')
    with pytest.raises(RuntimeError, match="가짜 규칙 모듈 실행 실패"):
        TreeRule(rule)


def test_limits_are_bounded():
    from deploy_server.fake_provider import LIMITS_KEPT, FakeProvider
    p = FakeProvider()
    for _ in range(LIMITS_KEPT + 5):
        p.limits.append([])
    assert len(p.limits) == LIMITS_KEPT


def test_chat_takes_strict_and_seed_by_name_with_same_bytes(tmp_path):
    """RX-1(지시 034): strict · seed는 이름 인자로 받기만 — 옛 규칙 · 트리 규칙 모두 답 바이트 그대로.
    옛 호출 모양(packrt 0.10.0 engine: 위치 인자 6개)도 그대로."""
    from deploy_server.fake_provider import FakeProvider, TreeRule
    rule = tmp_path / "rule.py"
    rule.write_text('import json\nFORMAT = "echo"\nclass FakeRefused(ValueError):\n    limits = ["refused"]\n'
                    'def load_known():\n    return ()\ndef respond_text(prompt):\n    return json.dumps({"p": prompt}), []\n')
    ev = [{"id": "H-1", "source_type": "history", "distance": 0.2, "failure_type": "TWF"}, {"id": "M-1", "source_type": "manual"}]
    msgs = [{"role": "user", "content": f"{DRAFT_HEADING}\n{json.dumps(ev)}\n"}]
    for make in (FakeProvider, lambda: FakeProvider(rule=TreeRule(rule))):
        base = make().chat("openai", "m", msgs)
        assert make().chat("openai", "m", msgs, None, 0, 256) == base
        assert make().chat("openai", "m", msgs, strict=True, seed=7) == base
        assert make().chat("openai", "m", msgs, {"type": "object"}, 0, 256, strict=False, seed=None) == base
        assert make().chat("openai", "m", msgs, seed=7, reasoning_effort="low") == base   # packrt 0.14.0 D-475: 받기만
    assert json.loads(base[0]) == {"p": msgs[0]["content"]}
    with pytest.raises(TypeError):
        FakeProvider().chat("openai", "m", msgs, None, 0, 256, True)   # strict는 이름으로만
    import inspect
    from packrt.run import engine
    if hasattr(engine, "_takes"):   # packrt 0.14.0+: 엔진이 이 칸을 받는 provider로 보는지(없으면 node 오류 provider_option)
        assert engine._takes(FakeProvider().chat, "reasoning_effort")
    assert "reasoning_effort" in inspect.signature(FakeProvider.chat).parameters


STRICT_OK = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {"a": {"type": "string"}},
             "required": ["a"], "additionalProperties": False}
STRICT_BAD = {"type": "object", "properties": {"a": {"type": "string"}}}   # required · additionalProperties false 없음


def test_strict_400_only_when_the_schema_has_strict_problems(monkeypatch):
    """RX-6(지시 035): strict이고 schema가 있을 때만 strict_schema_problems를 봄 — 뿌리 $schema · $id를 뺀 사본(packrt HttpProvider 0.11.0과 같음).
    오류가 있으면 실제 provider 400과 같은 모양의 ProviderError, 없으면 답 바이트 그대로. 설치본과 상관없이 길만 보는 시험."""
    import packrt.ir
    from packrt.run.providers import ProviderError
    from deploy_server.fake_provider import FakeProvider
    seen = []

    def problems(schema):
        seen.append(schema)
        return (["$: additionalProperties가 false가 아닙니다"] if schema.get("additionalProperties") is not False else []), ["$.x"]
    monkeypatch.setattr(packrt.ir, "strict_schema_problems", problems, raising=False)
    msgs = [{"role": "user", "content": f"{DRAFT_HEADING}\n[]\n"}]
    base = FakeProvider().chat("openai", "m", msgs, STRICT_OK)
    assert FakeProvider().chat("openai", "m", msgs, STRICT_OK, strict=True, seed=7) == base
    assert seen == [{k: v for k, v in STRICT_OK.items() if k != "$schema"}] and "$schema" in STRICT_OK
    with pytest.raises(ProviderError, match=r"^openai /chat/completions HTTP 400: .*invalid_request_error") as e:
        FakeProvider().chat("openai", "m", msgs, STRICT_BAD, strict=True)
    assert "additionalProperties" in str(e.value) and len(str(e.value).split("HTTP 400: ", 1)[1]) <= 300
    seen.clear()
    FakeProvider().chat("openai", "m", msgs, STRICT_BAD)                  # strict 아님 → 보지 않음
    FakeProvider().chat("openai", "m", msgs, None, strict=True)           # schema 없음 → 보지 않음
    assert seen == []


def test_strict_400_with_the_real_packrt_check():
    from packrt.run.providers import ProviderError
    from deploy_server.fake_provider import FakeProvider
    msgs = [{"role": "user", "content": f"{DRAFT_HEADING}\n[]\n"}]
    assert FakeProvider().chat("openai", "m", msgs, STRICT_OK, strict=True) == FakeProvider().chat("openai", "m", msgs, STRICT_OK)
    with pytest.raises(ProviderError, match="HTTP 400"):
        FakeProvider().chat("openai", "m", msgs, STRICT_BAD, strict=True)


def test_rule_module_can_read_files_beside_it(tmp_path):
    """규칙 모듈이 옆 파일을 읽어도 됨(c1a15-pack 9745f54 tools/fake/rule.py → split_known.json · D-556 덧 7 Pack 제안 1).
    배포단은 tools/fake/ 폴더째 꺼냄 — 모듈 하나만 꺼내면 옆 파일이 없어 배포 때 멈춤."""
    from deploy_server.fake_provider import load_rule
    repo = tmp_path / "pack"
    (repo / "tools" / "fake").mkdir(parents=True)
    (repo / "tools" / "fake" / "rule.py").write_text(
        "import json\nfrom pathlib import Path\nFORMAT = 'c1a15-pack/fake-rule@1'\nclass FakeRefused(ValueError):\n    limits = []\n"
        "KNOWN = Path(__file__).with_name('split_known.json')\n"
        "def load_known():\n    return json.loads(KNOWN.read_text(encoding='utf-8'))\n"
        "def respond_text(prompt):\n    return json.dumps(load_known().get(prompt.strip(), {'items': []}), ensure_ascii=False), []\n", encoding="utf-8")
    (repo / "tools" / "fake" / "split_known.json").write_text(json.dumps({"냉각수 보충": {"items": [{"check": {"what": "수위"}}]}}, ensure_ascii=False), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rule with a file beside it")
    commit = _git(repo, "rev-parse", "HEAD")
    rule = load_rule(repo, commit, tmp_path / "packs")
    assert rule.FORMAT == "c1a15-pack/fake-rule@1"
    assert json.loads(rule.respond_text("냉각수 보충")[0]) == {"items": [{"check": {"what": "수위"}}]}
    beside = tmp_path / "packs" / f"pack-{commit[:12]}-fake-rule" / "tools" / "fake" / "split_known.json"
    assert beside.is_file() and not beside.stat().st_mode & 0o222            # 꺼낸 사본은 읽기 전용
