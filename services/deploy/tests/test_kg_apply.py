"""scripts/kg_apply.py · scripts/graph_swap.py 가짜 시험(D-546 K6 · dev-2) — 서버 · 그래프 DB · 추출기 · 적재기 · Pack 저장소 모두 가짜, 네트워크 · DB 0.
방아쇠 실패 쪽 넷(편지 0 · 같은 편지 · 겹침 · 꼴 틀린 편지)과 바꿔 끼우기 실패 때 설정이 옛 값으로 돌아가는지를 봅니다.
F2 실제 칸(D-546 덧 32): 키 파일 없으면 가짜 · 있으면 --real 칸 · 상한 넘으면 부르지 않음 · provider 오류는 code · where · HTTP만 —
마지막 둘은 LLMOps dev-2 추출기 그대로를 127.0.0.1 가짜 provider에 붙여 봄(밖 연결 0 · 가짜 키)."""
import http.server
import importlib.util
import json
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REV1 = "sha256:" + "1" * 64
REV2 = "sha256:" + "2" * 64
CFG_TEXT = f'deployment_id: c1a15@dev2-local\npack:\n  ref: g1                 # 사본 태그\n  expect_revision: "{REV1}"\n'
LETTER = {"kind": "kg_record", "schema": 1, "report_id": "RPT-000001", "approved_at": "2026-11-03T19:51:10+09:00",
          "record": {"record_id": "R-0001", "exception_record": {"situation": "s", "action": "a", "context": {}}}}


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def K(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "dev2.yaml").write_text(CFG_TEXT)
    for k, v in {"AD_CONFIG": str(tmp_path / "config" / "dev2.yaml"), "AD_PORT": "28896", "AD_SERVE_LOG": str(tmp_path / "serve.log"),
                 "KG_INBOX": str(tmp_path / "inbox"), "KG_STATE": str(tmp_path / "state")}.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "inbox").mkdir()
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    for n in ("autodeploy", "graph_swap"):
        sys.modules.pop(n, None)
    m = load("kg_apply_under_test", "kg_apply.py")
    m.calls = {"extract": 0, "load": 0, "swap": [], "new_revision": 0}
    m.loader_tag = "L2"
    m.loader_rejected = []
    m.load_wait = 0.0
    monkeypatch.setattr(m, "running", lambda: ("g1", REV1, "L1"))

    def extract(new, tag):
        m.calls["extract"] += 1
        return 0

    def fake_load(tag=None, base_text=None):
        m.calls["load"] += 1
        time.sleep(m.load_wait)
        res = {"tag": m.loader_tag, "release_id": f"graph-{m.loader_tag}", "rejected": list(m.loader_rejected)}
        with open(m.STATE / "rejected.jsonl", "a") as f:   # 진짜 load()의 기록 줄과 같은 꼴
            for x in res["rejected"]:
                f.write(json.dumps({"letter": x}, ensure_ascii=False) + "\n")
        return res

    def new_revision(tag_from, fixed=None, expect=None):
        m.calls["new_revision"] += 1
        return "g2"

    m.real_extract = m.extract
    monkeypatch.setattr(m, "extract", extract)
    monkeypatch.setattr(m, "load", fake_load)
    monkeypatch.setattr(m, "new_revision", new_revision)
    monkeypatch.setattr(m.S, "swap", lambda tag: m.calls["swap"].append(tag) or REV2)
    return m


def letter(K, name="RPT-000001", **kw):
    (K.INBOX / f"{name}.json").write_text(json.dumps({**LETTER, "report_id": name, **kw}, ensure_ascii=False))


def test_no_letter_does_nothing(K):
    assert K.main() == "none"
    assert K.calls == {"extract": 0, "load": 0, "swap": [], "new_revision": 0}


def test_one_letter_swaps_once_then_same_letter_zero(K):
    letter(K)
    assert K.main() == "g2"
    assert K.calls["swap"] == ["g2"] and (K.STATE / "done" / "RPT-000001").exists()
    assert K.main() == "none"                                   # 같은 편지로 다시 부름
    assert K.calls["swap"] == ["g2"] and K.calls["load"] == 1


def test_overlapping_calls_run_one_at_a_time(K):
    letter(K)
    K.load_wait = 0.5
    out = {}
    t1 = threading.Thread(target=lambda: out.__setitem__("a", K.main()))
    t1.start()
    time.sleep(0.1)
    t2 = threading.Thread(target=lambda: out.__setitem__("b", K.main()))
    t2.start()
    t1.join()
    t2.join()
    assert sorted(out.values()) == ["g2", "none"]
    assert K.calls["swap"] == ["g2"] and K.calls["load"] == 1
    log = (K.STATE / "kg-apply.log").read_text()
    waits = [float(l.split("기다림 ")[1].split("초")[0]) for l in log.splitlines() if "잠금 얻음" in l]
    assert max(waits) >= 0.3                                    # 둘째는 첫째가 끝날 때까지 기다림


def test_broken_letter_listed_no_swap(K):
    letter(K, "RPT-900001", record={"record_id": "R-9001"})
    K.loader_tag = "L1"                                         # 적재기가 그 편지를 빼면 지금 도는 그래프와 같음
    K.loader_rejected = ["RPT-900001.json"]
    assert K.main() == "same"
    assert K.calls["swap"] == [] and K.calls["new_revision"] == 0
    assert (K.STATE / "done" / "RPT-900001").exists()
    rows = [json.loads(l) for l in (K.STATE / "rejected.jsonl").read_text().splitlines()]
    assert rows == [{"letter": "RPT-900001.json"}]


def test_extract_needs_answers_for_exception_letter(K, monkeypatch, tmp_path):
    real = load("kg_apply_real_extract", "kg_apply.py")
    monkeypatch.delenv("KG_ANSWERS", raising=False)
    monkeypatch.delenv("KG_EXGRAPH", raising=False)
    with pytest.raises(real.ApplyError, match="KG_ANSWERS"):
        real.extract([(Path("x.json"), LETTER)], "g1")
    assert real.extract([(Path("y.json"), {"report_id": "RPT-2", "record": {"record_id": "H-1"}})], "g1") == 0   # 예외 기록 없는 편지는 추출 0


def test_extract_merges_once_per_id(K, monkeypatch, tmp_path):
    real = load("kg_apply_real_merge", "kg_apply.py")
    monkeypatch.setenv("KG_ANSWERS", str(tmp_path / "a.json"))
    monkeypatch.setenv("KG_EXGRAPH", str(tmp_path / "exgraph.py"))
    monkeypatch.setattr(real, "git", lambda *a: "{}")
    rows = {"sections": [{"no": "EX-R-0001"}], "exception_relations": [{"id": "EX-R-0001"}]}

    def sh(args, **kw):
        Path(args[args.index("--out") + 1]).write_text(json.dumps(rows))
        return types.SimpleNamespace(returncode=0, stdout='{"added": ["R-0001"]}', stderr="")
    monkeypatch.setattr(real.A, "sh", sh)
    real.STATE.mkdir(parents=True, exist_ok=True)
    assert real.extract([(Path("x.json"), LETTER)], "g1") == 1
    assert real.extract([(Path("x.json"), LETTER)], "g1") == 0   # 같은 관계 id는 한 번만
    acc = json.loads((real.STATE / "nodes.json").read_text())
    assert len(acc["sections"]) == 1 and len(acc["exception_relations"]) == 1


@pytest.fixture
def S(tmp_path, monkeypatch):
    (tmp_path / "dev2.yaml").write_text(CFG_TEXT)
    monkeypatch.setenv("AD_CONFIG", str(tmp_path / "dev2.yaml"))
    monkeypatch.setenv("AD_PORT", "28896")
    monkeypatch.setenv("AD_SERVE_LOG", str(tmp_path / "serve.log"))
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    for n in ("autodeploy",):
        sys.modules.pop(n, None)
    m = load("graph_swap_under_test", "graph_swap.py")
    monkeypatch.setattr(m, "rev", lambda tag, repo=None: ("c" * 40, REV2, "L2"))
    m.killed = []
    monkeypatch.setattr(m.A, "listener", lambda: "4242")
    monkeypatch.setattr(m.A, "info_line", lambda: ({}, REV1))
    monkeypatch.setattr(m, "_term", lambda pid: m.killed.append(pid))
    return m


def test_swap_check_fails_keeps_config_and_server(S, monkeypatch, tmp_path):
    monkeypatch.setattr(S.A, "sh", lambda args, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(S.SwapError, match="묶기 대조"):
        S.swap("g2")
    assert (tmp_path / "dev2.yaml").read_text() == CFG_TEXT and S.killed == []


def test_swap_server_not_up_restores_config(S, monkeypatch, tmp_path):
    monkeypatch.setattr(S.A, "sh", lambda args, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    seen = []

    def serve():
        seen.append((tmp_path / "dev2.yaml").read_text())
        return len(seen) > 1                                       # 새 설정으로는 안 뜨고, 옛 설정으로는 뜸
    monkeypatch.setattr(S, "_serve", serve)
    with pytest.raises(S.SwapError, match="옛 서버 다시 뜸"):
        S.swap("g2")
    assert "ref: g2" in seen[0] and seen[1] == CFG_TEXT
    assert (tmp_path / "dev2.yaml").read_text() == CFG_TEXT and S.killed == ["4242"]


def test_swap_ok_changes_two_fields(S, monkeypatch, tmp_path):
    monkeypatch.setattr(S.A, "sh", lambda args, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(S, "_serve", lambda: True)
    infos = iter([({}, REV2)])
    monkeypatch.setattr(S.A, "info_line", lambda: next(infos))
    assert S.swap("g2") == REV2
    t = (tmp_path / "dev2.yaml").read_text()
    assert "ref: g2 " in t and REV2 in t and REV1 not in t


FAKE_KEY = "fake-key-not-real-7c1d"


def exgraph_source():
    """LLMOps dev-2 추출기(옆 저장소 dev-2 가지 또는 dev-2 worktree). 없으면 None."""
    r = subprocess.run(["git", "-C", str(ROOT.parent / "llmops-lite-v2"), "show", "dev-2:llmops/exgraph.py"], capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout
    wt = ROOT.parent / "llmops-lite-v2-dev2" / "llmops" / "exgraph.py"
    return wt.read_text(encoding="utf-8") if wt.is_file() else None


@pytest.fixture
def R(tmp_path, monkeypatch):
    """진짜 extract()(추출기 부르는 칸) · git만 가짜."""
    for k in ("KG_KEY_FILE", "KG_ANSWERS", "KG_CAP", "KG_MODEL", "KG_BASE_URL", "KG_KEY_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KG_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("KG_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("KG_EXGRAPH", str(tmp_path / "exgraph.py"))
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    m = load("kg_apply_real_fields", "kg_apply.py")
    m.STATE.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, "git", lambda *a: "{}")
    (tmp_path / "key.env").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n")
    return m


def spy(m, monkeypatch, stdout='{"added": [], "skipped": []}'):
    seen = []

    def sh(args, **kw):
        seen.append(args)
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(m.A, "sh", sh)
    return seen


def test_no_key_file_stays_fake(R, monkeypatch, tmp_path):
    monkeypatch.setenv("KG_ANSWERS", str(tmp_path / "a.json"))
    seen = spy(R, monkeypatch)
    R.extract([(Path("x.json"), LETTER)], "g1")
    assert "--answers" in seen[0] and "--real" not in seen[0] and "--key-file" not in seen[0]


def test_key_file_gives_real_fields_path_only(R, monkeypatch, tmp_path):
    monkeypatch.setenv("KG_KEY_FILE", str(tmp_path / "key.env"))
    monkeypatch.setenv("KG_ANSWERS", str(tmp_path / "a.json"))           # 둘 다 있으면 실제 칸이 이김(가짜 답은 넘기지 않음)
    seen = spy(R, monkeypatch)
    R.extract([(Path("x.json"), LETTER)], "g1")
    a = seen[0]
    assert "--real" in a and "--answers" not in a
    assert a[a.index("--key-file") + 1] == str(tmp_path / "key.env") and a[a.index("--key-name") + 1] == "OPENAI_API_KEY"
    assert a[a.index("--model") + 1] == "gpt-4.1-mini" and a[a.index("--cap") + 1] == "0.05"
    assert a[a.index("--base-url") + 1] == "https://api.openai.com/v1"
    assert FAKE_KEY not in " ".join(a) and FAKE_KEY not in (R.STATE / "kg-apply.log").read_text()


@pytest.mark.parametrize("env, why", [({"KG_KEY_FILE": "없는-파일.env"}, "파일 없음"), ({"KG_CAP": "x"}, "숫자"), ({"KG_CAP": "2"}, "1 이하"),
                                      ({"KG_CAP": "0"}, "1 이하")])
def test_real_fields_refused_before_call(R, monkeypatch, tmp_path, env, why):
    monkeypatch.setenv("KG_KEY_FILE", str(tmp_path / "key.env"))
    for k, v in env.items():
        monkeypatch.setenv(k, v if k != "KG_KEY_FILE" else str(tmp_path / v))
    seen = spy(R, monkeypatch)
    with pytest.raises(R.ApplyError, match=why):
        R.extract([(Path("x.json"), LETTER)], "g1")
    assert seen == []


def test_letter_without_exception_calls_nothing(R, monkeypatch):
    seen = spy(R, monkeypatch)
    assert R.extract([(Path("y.json"), {"report_id": "RPT-2", "record": {"record_id": "H-1"}})], "g1") == 0 and seen == []


class Provider(http.server.BaseHTTPRequestHandler):
    hits, status = [], 401

    def do_POST(self):
        Provider.hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({"error": {"message": f"Incorrect API key provided: {FAKE_KEY}", "code": "invalid_api_key"}}).encode()
        self.send_response(Provider.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def provider(R, monkeypatch, tmp_path):
    src = exgraph_source()
    if src is None:
        pytest.skip("LLMOps dev-2 추출기(exgraph.py)가 없음")
    (tmp_path / "exgraph.py").write_text(src, encoding="utf-8")
    Provider.hits = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("KG_KEY_FILE", str(tmp_path / "key.env"))
    monkeypatch.setenv("KG_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    yield R
    srv.shutdown()


def test_over_cap_never_calls(provider, monkeypatch):
    monkeypatch.setenv("KG_CAP", "0.000001")
    with pytest.raises(provider.ApplyError, match="상한 — 부르지 않음"):
        provider.extract([(Path("x.json"), LETTER)], "g1")
    assert Provider.hits == []
    assert "budget_cap" in (provider.STATE / "kg-apply.log").read_text()


def test_provider_error_code_where_http_only(provider):
    with pytest.raises(provider.ApplyError, match="추출기 실패"):
        provider.extract([(Path("x.json"), LETTER)], "g1")
    assert Provider.hits == ["/v1/chat/completions"]
    log = (provider.STATE / "kg-apply.log").read_text()
    assert "extract_http" in log and "HTTP 401" in log
    assert FAKE_KEY not in log and "Incorrect API key" not in log


def test_loader_gets_running_graph_as_base(R, monkeypatch, tmp_path):
    """적재기는 지금 도는 태그의 그래프에서 예외 줄을 이어받음(Pack bef4c68~) — 시작 판의 EX-R-0001이 다음 판에서 사라지지 않게."""
    monkeypatch.setenv("KG_HISTORY", str(tmp_path / "h.jsonl"))
    asked = []
    monkeypatch.setattr(R, "git", lambda *a: asked.append(a) or '{"layer": "L-start-ex1"}')
    monkeypatch.setattr(R.S, "pack_repo", lambda: tmp_path)
    seen = spy(R, monkeypatch, stdout='{"tag": "L-start-ex1", "rejected": []}')
    R.load("g3")
    a = seen[0]
    assert ("show", "g3:pack/templates/graph/current.json") in asked
    assert json.loads(Path(a[a.index("--base") + 1]).read_text()) == {"layer": "L-start-ex1"}


def exgraph_says(K, monkeypatch, tmp_path, stdout):
    """K에 진짜 extract()를 되돌리고 추출기(subprocess)만 stdout 한 줄 · 줄 파일 없음으로 가짜."""
    monkeypatch.setattr(K, "extract", K.real_extract)
    monkeypatch.setenv("KG_ANSWERS", str(tmp_path / "a.json"))
    monkeypatch.setenv("KG_EXGRAPH", str(tmp_path / "exgraph.py"))
    monkeypatch.setattr(K, "git", lambda *a: "{}")
    monkeypatch.setattr(K.A, "sh", lambda args, **kw: types.SimpleNamespace(returncode=0, stdout=stdout, stderr=""))


def test_nothing_to_add_is_success_no_swap(K, monkeypatch, tmp_path):
    """D-546 덧 34 · packrt-1 146: 앱 기록 번호가 시작 그래프의 시험 기록과 겹쳐 추출기가 already_in만 냄 → 성공 · 바꿈 0 · 다시 띄움 0 · done."""
    letter(K, "RPT-000002", record={**LETTER["record"], "record_id": "R-0002"})
    exgraph_says(K, monkeypatch, tmp_path, '{"added": [], "skipped": [{"record_id": "R-0002", "code": "already_in"}], "notices": [], "usage": null}')
    assert K.main() == "same"
    assert K.calls["load"] == 0 and K.calls["new_revision"] == 0 and K.calls["swap"] == []
    assert (K.STATE / "done" / "RPT-000002").exists()
    assert "더할 것 없음" in (K.STATE / "kg-apply.log").read_text()
    assert K.main() == "none"                                   # 같은 편지로 다시 부르면 새 편지 0


def test_already_in_with_other_skip_still_stops(K, monkeypatch, tmp_path):
    letter(K, "RPT-000002", record={**LETTER["record"], "record_id": "R-0002"})
    letter(K, "RPT-000003", record={**LETTER["record"], "record_id": "R-0003"})
    exgraph_says(K, monkeypatch, tmp_path, '{"added": [], "skipped": [{"record_id": "R-0002", "code": "already_in"}, '
                                           '{"record_id": "R-0003", "code": "no_answer"}]}')
    with pytest.raises(SystemExit, match="추출기가 뺀 기록 1건"):
        K.main()
    assert K.calls["swap"] == [] and K.calls["load"] == 0 and not list((K.STATE / "done").iterdir())


def test_already_in_beside_plain_letter_goes_to_loader(K, monkeypatch, tmp_path):
    letter(K, "RPT-000002", record={**LETTER["record"], "record_id": "R-0002"})
    letter(K, "RPT-000004", record={"record_id": "H-0004"})              # 예외 기록 없는 편지(이력만)
    exgraph_says(K, monkeypatch, tmp_path, '{"added": [], "skipped": [{"record_id": "R-0002", "code": "already_in"}]}')
    assert K.main() == "g2"
    assert K.calls["load"] == 1 and K.calls["swap"] == ["g2"]
    assert sorted(x.name for x in (K.STATE / "done").iterdir()) == ["RPT-000002", "RPT-000004"]


# Pack bef4c68 적재기의 이어받기 약속만 흉내(loader.py:13 · 173): 기준 = --base, 없으면 --out 파일 · 기준의 예외 줄 + --nodes 줄을 id로 합침.
FAKE_LOADER = r'''
import argparse, json
from pathlib import Path
ap = argparse.ArgumentParser()
for x in ("--history", "--inbox", "--nodes", "--base", "--out"):
    ap.add_argument(x)
a = ap.parse_args()
base = Path(a.base or a.out)
old = json.loads(base.read_text())["exception_relations"] if base.is_file() else []
new = json.loads(Path(a.nodes).read_text())["exception_relations"] if a.nodes else []
rels = {x["id"]: x for x in old + new}
out = {"layer": f"L-ex{len(rels)}", "exception_relations": list(rels.values())}
Path(a.out).write_text(json.dumps(out))
print(json.dumps({"tag": out["layer"], "release_id": "r", "rejected": []}))
'''


def test_new_graph_keeps_running_exception_lines(R, monkeypatch, tmp_path):
    """D-546 덧 35 · packrt-1 147: 도는 그래프 ex6(EX-R-0001 ~ 0006) + 새 승인 EX-R-0008 → 새 그래프 ex7. --base 없으면 ex1(기존 여섯 잃음)."""
    running = {"layer": "L-ex6", "exception_relations": [{"id": f"EX-R-000{i}"} for i in range(1, 7)]}
    monkeypatch.setattr(R, "git", lambda *a: json.dumps(running))
    monkeypatch.setattr(R.S, "pack_repo", lambda: tmp_path)
    (tmp_path / "loader.py").write_text(FAKE_LOADER)
    monkeypatch.setenv("KG_LOADER_PY", sys.executable)
    monkeypatch.setenv("KG_LOADER", str(tmp_path / "loader.py"))
    monkeypatch.setenv("KG_HISTORY", str(tmp_path / "h.jsonl"))
    (R.STATE / "nodes.json").write_text(json.dumps({"sections": [], "exception_relations": [{"id": "EX-R-0008"}]}))
    res = R.load("g1")
    assert res["tag"] == "L-ex7"
    ids = [x["id"] for x in json.loads((R.STATE / "current.json").read_text())["exception_relations"]]
    assert ids == [f"EX-R-000{i}" for i in range(1, 7)] + ["EX-R-0008"]
