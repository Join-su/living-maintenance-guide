"""R12 새 이력 자동 반영(VFE EV-005 지시 046 · D-283, R12-E1-E3.md R-1 ~ R-5).
- R-1 · R-2(지금 packrt로): vector store의 `releases` 목록 받기 · 실행 설정의 둘째 찾는 곳 — 새 칸이 없으면 실행 설정 바이트 그대로.
- R-3 · R-5(packrt 0.12.0 이상 — `packrt.index`가 있을 때만, D-416으로 .venv에 0.12.5 설치 · D-480 덧 4 repin 0.15.0):
  견본 index_h0의 이력 version(corpus v1, 문서 3)으로 배포 → 받은 편지 한 건 → scripts/r12-update → 새 corpus · 새 version →
  운영 설정 store release 한 칸 → 같은 배포를 다시 띄우면 drs가 바뀌고 다음 첫 Run이 새 문서를 찾음. 실패 길은 운영 설정 그대로."""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from deploy_server.execution import ExecutionError, check_execution
from deploy_server.pack_source import PackCopy, write_execution_config

from conftest import ROOT

try:
    import packrt.index  # noqa: F401
    HAS_INDEX = True
except ImportError:
    HAS_INDEX = False
PRT = ROOT.parent / "agent-pack-runtime"


# ------------------------------------------------------------------ R-1
def test_r1_vector_store_takes_a_releases_list():
    ex = {"entrypoint": "g", "stores": {"history": {"backend": "flat-local", "release": "ir-a", "releases": ["ir-b"], "embedding": {"mode": "fake"}}}}
    assert check_execution(ex) is False
    for bad in ([], "ir-b", [""], [3]):
        with pytest.raises(ExecutionError, match="releases"):
            check_execution({"entrypoint": "g", "stores": {"history": {"backend": "flat-local", "release": "ir-a", "releases": bad}}})
    with pytest.raises(ExecutionError, match="받지 않는 항목"):   # table store는 그대로 안 받음
        check_execution({"entrypoint": "g", "stores": {"t": {"backend": "sqlite-file", "release": "x", "releases": ["y"]}}})


# ------------------------------------------------------------------ R-2
def _copy(tmp_path):
    return PackCopy(repo=tmp_path, ref="v1", commit="0" * 40, tag="v1", root=tmp_path, pack_dir=tmp_path / "pack", releases_dir=tmp_path / "releases")


def test_r2_execution_config_bytes_unchanged_without_extra_roots(tmp_path):
    ex = {"entrypoint": "g", "stores": {"h": {"backend": "flat-local", "release": "ir-a"}}, "models": {}}
    a = write_execution_config(tmp_path / "a.yaml", _copy(tmp_path), ex).read_bytes()
    b = write_execution_config(tmp_path / "b.yaml", _copy(tmp_path), ex, []).read_bytes()
    assert a == b and b'release_roots: ["' + str(tmp_path / "releases").encode() + b'"]' in a


def test_r2_extra_root_and_releases_list_are_written(tmp_path):
    ex = {"entrypoint": "g", "stores": {"h": {"backend": "flat-local", "release": "ir-a", "releases": ["ir-b", "ir-c"]}}, "models": {}}
    text = write_execution_config(tmp_path / "c.yaml", _copy(tmp_path), ex, [tmp_path / "built"]).read_text()
    assert f'release_roots: ["{tmp_path / "releases"}", "{tmp_path / "built"}"]' in text
    assert 'h: {backend: "flat-local", release: "ir-a", releases: ["ir-b", "ir-c"]}' in text


# ------------------------------------------------------------------ R-3 · R-5
H0 = PRT / "tests" / "fixtures" / "index_h0"
EMB = {"provider": "openai", "model": "text-embedding-3-small"}
SENSORS = ["torque_nm", "tool_wear_min"]
BASE_V = "ir-history-c6510147"   # 견본: corpus hc-h0-history-v1(문서 3) · split record


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _pack_repo(tmp_path, node_index=None, store_metadata=None):
    """retrieve@2 이력 store 하나를 읽는 시험 Pack(packrt 시험 test_index_select와 같은 store 정의) + 견본 release(태그 v1)."""
    import packrt
    repo = tmp_path / "r12pack"
    pack = repo / "pack"
    (pack / "schemas").mkdir(parents=True)
    (pack / "schemas" / "q.json").write_text(json.dumps({"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}))
    (pack / "schemas" / "hit.json").write_text(json.dumps({"type": "object"}))
    (pack / "schemas" / "note.json").write_text(json.dumps({"type": "string"}))
    (pack / "templates").mkdir()
    (pack / "templates" / "n.md").write_text("done\n")
    find = {"id": "find_similar", "kind": "retrieve@2", "params": {"store": "history", "query": "{state.q.text}", "top_k": 5},
            "reads": ["q"], "writes": ["similar"]}
    if node_index is not None:   # D-291 시험: node recipe를 release와 다르게
        find["params"]["index"] = node_index
    nodes = [find,
             {"id": "fin", "kind": "render@1", "params": {"template": "templates/n.md", "format": "md", "to": "note"}, "reads": ["q"], "writes": ["note"]}]
    doc = {"ir_version": 1, "runtime_contract": {"min_runtime_version": packrt.__version__}, "pack": {"id": "r12", "version": "V0", "title": "r12"},
           "env": {"required": [], "optional": []},
           "stores": {"history": {"type": "vector", "index": {"embedding": EMB, "split": "record", "metadata": store_metadata or (["symptom_group", "source"] + SENSORS),
                                                              "rerank_norm": {"fields": SENSORS, "from": {"source": "generated"}}}}},
           "schemas": {"Q": "schemas/q.json", "Hit": "schemas/hit.json", "Note": "schemas/note.json"},
           "graphs": {"g": {"state": {"q": "Q", "note": "Note", "similar": {"type": "list[Hit]"}}, "nodes": nodes,
                            "edges": [["START", "find_similar"], ["find_similar", "fin"], ["fin", "END"]]}},
           "entrypoints": [{"graph": "g", "input": "Q", "output": "Note"}], "trace": {"contract": 1}}
    (pack / "pack.yaml").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    shutil.copytree(H0 / "indexes" / BASE_V, repo / "releases" / "indexes" / BASE_V)
    shutil.copytree(H0 / "corpus" / "hc-h0-history-v1", repo / "releases" / "corpus" / "hc-h0-history-v1")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "r12 test pack")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "tag", "-a", "v1", "-m", "v1")
    return repo


def _config(tmp_path, repo, registration=None):
    """config/와 같은 깊이를 흉내 내려고 시험 저장소 뿌리 아래 config/ · config-live/를 씀(r12-update는 ROOT/config-live를 씀 → 시험은 ROOT를 바꿔 부름)."""
    root = tmp_path / "deploy"
    (root / "config").mkdir(parents=True)
    cfg = {"deployment_id": "r12@dev-local", "retention_class": "dev",
           "pack": {"repo": str(repo), "ref": "v1", "pack_subdir": "pack", "releases_subdir": "releases"},
           "execution": {"entrypoint": "g", "stores": {"history": {"backend": "flat-local", "release": BASE_V, "embedding": {"mode": "fake"}}}, "models": {}},
           "data_dir": str(tmp_path / "data"), "packs_dir": str(tmp_path / "packs"), "provider": "fake",
           "registration": registration or {"mode": "none"}, "grounding_keys": ["similar"], "secret_env_names": []}
    p = root / "config" / "r12-dev-local.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return root, p


def _run_script(root, *args):
    """scripts/r12-update를 시험 뿌리(ROOT 대신)로 부름: 같은 파일을 시험 뿌리에 복사하고 deploy_server는 PYTHONPATH로."""
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copy(ROOT / "scripts" / "r12-update", root / "scripts" / "r12-update")
    shutil.copy(ROOT / "scripts" / "serve.sh", root / "scripts" / "serve.sh")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(root / "scripts" / "r12-update"), *args], capture_output=True, text=True, env=env)


def _inbox(tmp_path, rid="RPT-0001"):
    box = tmp_path / "data" / "r12-inbox"
    box.mkdir(parents=True, exist_ok=True)
    v1 = {json.loads(l)["doc"] for l in (H0 / "corpus" / "hc-h0-history-v1" / "documents.jsonl").read_text().splitlines() if l.strip()}
    new = next(json.loads(l) for l in (H0 / "corpus" / "hc-h0-history-v2" / "documents.jsonl").read_text().splitlines()
               if l.strip() and json.loads(l)["doc"] not in v1)   # 견본 v2가 더한 한 문서 = 승인 보고서 하나
    (box / f"{rid}.json").write_text(json.dumps({"report_id": rid, "approved_at": "2026-10-24T17:30:00+09:00", "document": new}, ensure_ascii=False))
    return new


def _deployment(path):
    from deploy_server.app import Deployment
    from deploy_server.settings import load_settings
    from fake_writer import FakeWriter
    s = load_settings(path)
    return Deployment(s, trace_writer=FakeWriter(s.data_dir / "trace"))


def _first_run(dep, text):
    from fastapi.testclient import TestClient
    from deploy_server.app import create_app
    c = TestClient(create_app(deployment=dep), raise_server_exceptions=False)
    r = c.post("/runs", content=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json", "Idempotency-Key": f"r12-{os.urandom(6).hex()}"})
    return r.status_code, r.json(), c.get("/info").json()


needs_index = pytest.mark.skipif(not (HAS_INDEX and H0.is_dir()), reason="packrt 0.12.0(packrt.index)과 견본 index_h0가 필요")


@needs_index
def test_r5_r12_end_to_end_flat_local(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    st, body, info = _first_run(_deployment(conf), "공구 마모")
    assert st == 201, body
    old_drs = info["data_release_id"]
    assert info["releases"]["stores"] == {"history": BASE_V}
    new = _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), "--graph", "g", "--node", "find_similar")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "이 작업이 띄운 서버가 아님" in r.stdout   # R-4: 남이 띄운(여기선 없음) 서버는 다시 시작하지 않음
    assert "적재" not in r.stdout   # U-3: flat-local store는 load를 부르지 않음
    live = root / "config-live" / conf.name
    doc = yaml.safe_load(live.read_text())
    new_v = doc["execution"]["stores"]["history"]["release"]
    assert new_v != BASE_V and new_v.startswith("ir-history-") and doc["release_roots_extra"]
    st2, body2, info2 = _first_run(_deployment(live), new["text"])   # 같은 배포를 운영 설정으로 다시 띄움 = 다음 첫 Run
    assert st2 == 201 and info2["releases"]["stores"] == {"history": new_v} and info2["data_release_id"] != old_drs
    before = body["result"]["grounding"].get("similar") or []
    hits = body2["result"]["grounding"].get("similar") or []
    assert len(before) == 3 and len(hits) == 4   # 옛 version 문서 3 → 새 version 4(top_k 5)
    assert any(new["record_id"] in json.dumps(h.get("locator"), ensure_ascii=False) for h in hits), [h.get("locator") for h in hits]   # 새 문서가 찾힘
    state = json.loads((tmp_path / "data" / "r12-state.json").read_text())
    assert state["done_reports"] == ["RPT-0001"] and state["history"][-1]["status"] == "restart_needed" and state["history"][-1]["drs"] == info2["data_release_id"]
    again = _run_script(root, "update", "--config", str(conf), "--graph", "g", "--node", "find_similar")
    assert again.returncode == 0 and "새 보고서 없음" in again.stdout   # 같은 보고서 두 번 = 한 번만


@needs_index
def test_r3_missing_ledger_row_keeps_the_live_config(tmp_path):
    import sqlite3
    repo = _pack_repo(tmp_path)
    ledger = tmp_path / "llmops.db"
    with sqlite3.connect(ledger) as c:   # 장부는 있으나 새 drs 줄이 없음(llmops 요청 길 EV-044 전)
        c.execute("CREATE TABLE deployment (deployment_id text, pack_id text, pack_revision text, data_release_id text, release_bindings text, "
                  "runtime_version text, pack_commit text, pack_tag text, config_sha256 text, decision_id integer, created_at real)")
    root, conf = _config(tmp_path, repo, registration={"mode": "llmops_db", "path": str(ledger)})
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), "--graph", "g", "--node", "find_similar")
    assert r.returncode == 1 and "장부에 새 drs 줄이 없습니다" in r.stdout
    live = yaml.safe_load((root / "config-live" / conf.name).read_text())
    assert live["execution"]["stores"]["history"]["release"] == BASE_V   # 운영 설정 그대로
    state = json.loads((tmp_path / "data" / "r12-state.json").read_text())
    assert state["done_reports"] == [] and state["history"][-1]["status"] == "failed"


@needs_index
def test_r3_bad_inbox_file_stops_before_anything(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    box = tmp_path / "data" / "r12-inbox"
    box.mkdir(parents=True)
    (box / "x.json").write_text(json.dumps({"report_id": "bad id with space", "document": {"text": "t"}}))
    r = _run_script(root, "update", "--config", str(conf), "--graph", "g", "--node", "find_similar")
    assert r.returncode == 1 and "받은 편지" in r.stdout
    assert not (tmp_path / "data" / "releases").exists()   # 아무것도 만들지 않음


# ------------------------------------------------------------------ D-287 받은 편지 문서 칸(그 Pack 이력 store 정의로 검사)
C1A15_SENSORS = ["air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min", "temp_diff_k", "power_w"]


def _r12_module():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("r12_update_mod", str(ROOT / "scripts" / "r12-update"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def _c1a15_shaped_copy(tmp_path):
    """C1-a15 이력 store 모양(c1a15-pack 보고 012-02 표 — 이름은 PS-02 잠정): metadata = evidence_type · source · symptom_group · 센서 7칸 · cause,
    rerank_norm.fields = 센서 7칸, filterable = symptom_group."""
    pack = tmp_path / "pack"
    pack.mkdir(parents=True)
    doc = {"stores": {"history": {"type": "vector", "filterable": ["symptom_group"],
                                  "index": {"split": "record", "metadata": ["evidence_type", "source", "symptom_group", "cause"] + C1A15_SENSORS,
                                            "rerank_norm": {"fields": C1A15_SENSORS, "from": {"source": "generated"}}}}}}
    (pack / "pack.yaml").write_text(yaml.safe_dump(doc))
    return PackCopy(repo=tmp_path, ref="v", commit="0" * 40, tag="v", root=tmp_path, pack_dir=pack, releases_dir=tmp_path / "releases")


def _d287_doc(**md):
    base = {"evidence_type": "history", "source": "approved_report", "symptom_group": "TW", "cause": "TW-1",
            "air_temperature_k": 300.1, "process_temperature_k": 310.2, "rotational_speed_rpm": 1500, "torque_nm": 41.0,
            "tool_wear_min": 210, "temp_diff_k": 10.1, "power_w": 6440.4}
    base.update(md)
    return {"doc": "R-0123", "record_id": "R-0123", "text": "승인 보고서 서술", "metadata": {k: v for k, v in base.items() if v != "__drop__"}}


def test_d287_inbox_document_passes_the_pack_store_check(tmp_path):
    m = _r12_module()
    m.check_documents(_c1a15_shaped_copy(tmp_path), "history", [("RPT-1", {"document": _d287_doc()})])
    m.check_documents(_c1a15_shaped_copy(tmp_path / "b"), "history", [("RPT-2", {"document": _d287_doc(cause=None)})])   # 확정 원인 없음 = null 허용


@pytest.mark.parametrize("doc, why", [
    (_d287_doc(power_w="__drop__"), "power_w"),
    (_d287_doc(evidence_type="__drop__"), "evidence_type"),
    (_d287_doc(cause="__drop__"), "cause"),
    (_d287_doc(torque_nm="41"), "숫자"),
    ({**_d287_doc(), "doc": "R-9999"}, "doc = record_id"),
])
def test_d287_inbox_document_refused_when_a_pack_field_is_missing(tmp_path, doc, why):
    m = _r12_module()
    with pytest.raises(m.Stop, match=why):
        m.check_documents(_c1a15_shaped_copy(tmp_path), "history", [("RPT-1", {"document": doc})])



# ------------------------------------------------------------------ D-291 묶기 대조(쓰기 전 · 등록 전)
@needs_index
def test_d291_check_passes_for_a_binding_config(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    r = _run_script(root, "check", "--config", str(conf))
    assert r.returncode == 0 and "묶기 대조 통과" in r.stdout, r.stdout + r.stderr


@needs_index
def test_d291_check_stops_when_the_node_recipe_does_not_match_the_release(tmp_path):
    repo = _pack_repo(tmp_path, store_metadata=["source"] + SENSORS)   # store recipe metadata가 release(견본)와 다름 → index_missing
    root, conf = _config(tmp_path, repo)
    r = _run_script(root, "check", "--config", str(conf))
    assert r.returncode == 1 and "packrt 묶기 실패" in r.stdout and "index_missing" in r.stdout, r.stdout + r.stderr


@needs_index
def test_r4_restart_failure_restores_the_old_live_config(tmp_path, monkeypatch):
    """E3 R-4 멈춤 칸: 새 설정으로 다시 뜨지 않으면 옛 설정으로 되돌려 다시 띄움(운영 공백을 짧게). 서버 함수만 바꿔 끼우고 나머지는 진짜."""
    import argparse
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    m = _r12_module()
    monkeypatch.setattr(m, "ROOT", root)
    calls = []
    monkeypatch.setattr(m, "own_server", lambda s: {"pid": 1, "port": 8799, "live": "x"})
    monkeypatch.setattr(m, "stop_server", lambda s, wait_idle=60: calls.append("stop") or True)

    def fake_start(live, port, settings):
        calls.append(("start", yaml.safe_load(Path(live).read_text())["execution"]["stores"]["history"]["release"]))
        if len([c for c in calls if isinstance(c, tuple)]) == 1:
            raise m.Stop("서버가 뜨지 않음(시험)")
        return {"data_release_id": "drs:old", "deployment_id": "r12@dev-local", "releases": {}}
    monkeypatch.setattr(m, "start_server", fake_start)
    a = argparse.Namespace(config=str(conf), store="history", graph="g", node="find_similar", ledger_cmd=None, port=8799)
    with pytest.raises(m.Stop, match="옛 설정으로 되돌려 다시 띄움"):
        m.update(a)
    starts = [c[1] for c in calls if isinstance(c, tuple)]
    assert starts[0] != BASE_V and starts[1] == BASE_V   # 새 version으로 한 번 → 실패 → 옛 version으로 다시
    live = yaml.safe_load((root / "config-live" / conf.name).read_text())
    assert live["execution"]["stores"]["history"]["release"] == BASE_V
    state = json.loads((tmp_path / "data" / "r12-state.json").read_text())
    assert state["done_reports"] == [] and state["history"][-1]["status"] == "failed_after_config_changed"


# ------------------------------------------------------------------ 답장 112 묶음: R-3 시험 셋 · 밀린 보고서 · R-4 내 서버 · 기다림
def _empty_ledger(tmp_path):
    import sqlite3
    ledger = tmp_path / "llmops.db"
    with sqlite3.connect(ledger) as c:
        c.execute("CREATE TABLE deployment (deployment_id text, pack_id text, pack_revision text, data_release_id text, release_bindings text, "
                  "runtime_version text, pack_commit text, pack_tag text, config_sha256 text, decision_id integer, created_at real)")
    return ledger


def _state(tmp_path):
    return json.loads((tmp_path / "data" / "r12-state.json").read_text())


def _live_release(root, conf):
    return yaml.safe_load((root / "config-live" / conf.name).read_text())["execution"]["stores"]["history"]["release"]


def _inbox_extra(tmp_path, rid, record_id, text):
    """견본에 없는 둘째 승인 보고서(밀린 보고서 시험용) — 첫 보고서 문서와 같은 모양."""
    new = _inbox(tmp_path, rid)
    new = {**new, "doc": record_id, "record_id": record_id, "text": text}
    (tmp_path / "data" / "r12-inbox" / f"{rid}.json").write_text(json.dumps({"report_id": rid, "approved_at": "2026-10-24T18:00:00+09:00", "document": new}, ensure_ascii=False))
    return new


UPDATE = ("--graph", "g", "--node", "find_similar")


@needs_index
def test_r3_exists_on_recall_then_pending_reports_on_the_next_call(tmp_path):
    """장부 줄이 없어 멈춤 → 같은 묶음 다시 부름 = corpus · version 'exists'(다시 만들지 않음) → 그사이 보고서가 하나 더 옴 →
    장부 요청 명령(시험용 — 실제 모양은 llmops EV-044)이 줄을 넣는 다음 부름에서 밀린 두 보고서를 한 번에 반영."""
    repo = _pack_repo(tmp_path)
    ledger = _empty_ledger(tmp_path)
    root, conf = _config(tmp_path, repo, registration={"mode": "llmops_db", "path": str(ledger)})
    _inbox(tmp_path)
    r1 = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r1.returncode == 1 and "장부에 새 drs 줄이 없습니다" in r1.stdout, r1.stdout + r1.stderr
    assert "corpus built" in r1.stdout and "version built" in r1.stdout
    first = _state(tmp_path)["history"][-1]
    r2 = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r2.returncode == 1 and "corpus exists" in r2.stdout and "version exists" in r2.stdout, r2.stdout
    again = _state(tmp_path)["history"][-1]
    assert (again["corpus"], again["version"], again["drs"]) == (first["corpus"], first["version"], first["drs"])
    assert _live_release(root, conf) == BASE_V and _state(tmp_path)["done_reports"] == []
    late = _inbox_extra(tmp_path, "RPT-0002", "R-0901", "주축 진동이 커져 베어링을 바꿈. 교체 뒤 정상.")
    cmd = (f"{shlex.quote(sys.executable)} -c \"import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
           f"c.execute('INSERT INTO deployment (deployment_id, pack_revision, data_release_id) VALUES (?, NULLIF(?, \\'\\'), ?)', sys.argv[2:5]); c.commit()\" "
           f"{shlex.quote(str(ledger))} {{deployment_id}} {{pack_revision}} {{drs}}")
    r3 = _run_script(root, "update", "--config", str(conf), *UPDATE, "--ledger-cmd", cmd)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    st = _state(tmp_path)
    last = st["history"][-1]
    assert st["done_reports"] == ["RPT-0001", "RPT-0002"] and last["reports"] == ["RPT-0001", "RPT-0002"]
    assert last["corpus"] != first["corpus"] and last["drs"] != first["drs"] and "더함 2" in r3.stdout   # 묶음이 달라 새 corpus id(겹침 없음)
    assert _live_release(root, conf) == last["version"] != BASE_V
    st2, body, _ = _first_run(_deployment(root / "config-live" / conf.name), late["text"])
    assert st2 == 201 and len(body["result"]["grounding"]["similar"]) == 5   # 옛 3 + 밀린 2


@needs_index
def test_r3_corpus_conflict_keeps_the_live_config(tmp_path):
    """같은 보고서가 앞 시도 뒤 내용이 바뀌면 같은 corpus id · 다른 내용 = packrt corpus_conflict → 멈춤 · 운영 설정 그대로 · 기록에 이유."""
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo, registration={"mode": "llmops_db", "path": str(_empty_ledger(tmp_path))})
    new = _inbox(tmp_path)
    assert _run_script(root, "update", "--config", str(conf), *UPDATE).returncode == 1   # 장부 줄 없음 → corpus r1은 만들어 둠
    f = tmp_path / "data" / "r12-inbox" / "RPT-0001.json"
    f.write_text(json.dumps({"report_id": "RPT-0001", "approved_at": "2026-10-24T18:10:00+09:00", "document": {**new, "text": new["text"] + " (고쳐 씀)"}}, ensure_ascii=False))
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r.returncode == 1 and "corpus_conflict" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V
    st = _state(tmp_path)
    assert st["done_reports"] == [] and st["history"][-1]["status"] == "failed" and "corpus_conflict" in st["history"][-1]["reason"]


@needs_index
def test_r3_build_failure_keeps_the_live_config(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), "--graph", "g", "--node", "no_such_node")
    assert r.returncode == 1 and "packrt.index build 실패" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V
    st = _state(tmp_path)
    assert st["done_reports"] == [] and st["history"][-1]["status"] == "failed" and "build" in st["history"][-1]["reason"]


def _r4_settings(tmp_path):
    from types import SimpleNamespace
    (tmp_path / "data").mkdir(exist_ok=True)
    return SimpleNamespace(data_dir=tmp_path / "data", deployment_id="r12@dev-local")


def test_r4_own_server_needs_the_command_line_port_and_deployment_id(tmp_path, monkeypatch):
    """pid가 살아 있어도 명령 줄(uvicorn · 기록한 --port)과 그 포트 /info 배포 id가 맞아야 내 서버(pid 재사용 때 잘못 내리지 않게)."""
    m = _r12_module()
    s = _r4_settings(tmp_path)
    pidf = s.data_dir / "r12-server.pid"
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "uvicorn", "--port", "8799"])
    try:
        info = {"deployment_id": "r12@dev-local"}
        monkeypatch.setattr(m, "http", lambda port, path, timeout=5: info)
        pidf.write_text(json.dumps({"pid": p.pid, "port": 8799, "live": "x"}))
        assert m.own_server(s)["pid"] == p.pid
        info = {"deployment_id": "other@dev-local"}   # 같은 포트에 다른 배포
        assert m.own_server(s) is None
        info = {"deployment_id": "r12@dev-local"}
        pidf.write_text(json.dumps({"pid": p.pid, "port": 8798, "live": "x"}))   # 기록한 포트와 명령 줄 포트가 다름
        assert m.own_server(s) is None
        pidf.write_text(json.dumps({"pid": os.getpid(), "port": 8799, "live": "x"}))   # 살아 있지만 uvicorn이 아닌 프로세스(pid 재사용)
        assert m.own_server(s) is None

        def down(port, path, timeout=5):
            raise OSError("connection refused")
        monkeypatch.setattr(m, "http", down)
        pidf.write_text(json.dumps({"pid": p.pid, "port": 8799, "live": "x"}))   # 포트가 응답하지 않음
        assert m.own_server(s) is None
    finally:
        p.kill()
        p.wait()


def test_r4_stop_waits_while_a_run_is_running(tmp_path, monkeypatch):
    """실행 중인 Run이 있으면 끝날 때까지 기다린 뒤 내림(서버 · 시간은 바꿔 끼움)."""
    m = _r12_module()
    s = _r4_settings(tmp_path)
    (s.data_dir / "r12-server.pid").write_text("{}")
    monkeypatch.setattr(m, "own_server", lambda st: {"pid": 4242, "port": 8799, "live": "x"})
    seen = iter([[{"status": "running"}], [{"status": "running"}, {"status": "succeeded"}], [{"status": "succeeded"}]])
    log = []
    monkeypatch.setattr(m, "http", lambda port, path, timeout=5: log.append(("http", path)) or {"items": next(seen)})
    monkeypatch.setattr(m.time, "sleep", lambda sec: log.append(("sleep", sec)))
    monkeypatch.setattr(m.os, "killpg", lambda pid, sig: log.append(("killpg", pid)))

    def gone(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(m.os, "kill", gone)
    assert m.stop_server(s) is True
    assert [x[0] for x in log] == ["http", "sleep", "http", "sleep", "http", "killpg"]   # running 두 번 기다린 뒤에만 내림
    assert not (s.data_dir / "r12-server.pid").exists()


# ------------------------------------------------------------------ D-293 corpus 찾기 규칙(겹침 = 오류 · 기준 corpus hash 대조, find_releases 안 부름)
def test_d293_r12_does_not_call_the_inner_find_releases():
    """U-1(D-323): corpus는 공개 packrt.index.find_corpus로 찾고, 안쪽 이름 find_releases는 r12-update가 직접 부르지 않음."""
    src = (ROOT / "scripts" / "r12-update").read_text(encoding="utf-8")
    assert "import find_releases" not in src and "find_releases(" not in src
    assert "from packrt.index import IndexBuildError, find_corpus" in src and "find_corpus(roots, corpus_ref)" in src


@needs_index
def test_d293_duplicate_release_id_stops_before_anything(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    dup = tmp_path / "data" / "releases" / "dup" / "hc-h0-history-v1"   # 둘째 찾는 곳(extra) 2단 아래에 같은 id 폴더
    shutil.copytree(H0 / "corpus" / "hc-h0-history-v1", dup)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r.returncode == 1 and "기준 corpus 찾기 실패(corpus_ambiguous)" in r.stdout and "번호가 겹칩니다: hc-h0-history-v1" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V and not (tmp_path / "data" / "releases" / "corpus").exists()   # 새 corpus 안 만듦


def _retag_with_tampered_corpus(repo, fix_manifest):
    """Pack 저장소의 기준 corpus 문서를 바꿔 태그 v1을 다시 붙임. fix_manifest면 corpus manifest의 files · sha256도 새 내용에 맞춤
    (폴더 = manifest, 기준 version의 corpus_release.sha256만 다름)."""
    import hashlib
    from packrt.ir.releases import dir_hash
    c = repo / "releases" / "corpus" / "hc-h0-history-v1"
    docs = c / "documents.jsonl"
    docs.write_text(docs.read_text(encoding="utf-8").replace("0.35mm", "0.50mm"), encoding="utf-8")
    if fix_manifest:
        m = json.loads((c / "manifest.json").read_text(encoding="utf-8"))
        m["files"]["documents.jsonl"] = hashlib.sha256(docs.read_bytes()).hexdigest()
        m["sha256"] = dir_hash(c)
        (c / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "tamper")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "tag", "-f", "-a", "v1", "-m", "v1")


@needs_index
@pytest.mark.parametrize("fix_manifest", [False, True], ids=["folder-vs-manifest", "manifest-vs-base-version"])
def test_d293_base_corpus_hash_mismatch_stops(tmp_path, fix_manifest):
    repo = _pack_repo(tmp_path)
    _retag_with_tampered_corpus(repo, fix_manifest)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r.returncode == 1 and "기준 corpus 찾기 실패(index_invalid)" in r.stdout and "hc-h0-history-v1" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V and not (tmp_path / "data" / "releases" / "corpus").exists()


@needs_index
def test_u1_missing_base_corpus_stops(tmp_path):
    """U-1(D-323): 기준 version이 가리키는 corpus가 어느 찾는 곳에도 없으면 corpus_missing으로 멈춤 · 운영 설정 그대로."""
    repo = _pack_repo(tmp_path)
    shutil.rmtree(repo / "releases" / "corpus" / "hc-h0-history-v1")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "no corpus")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "tag", "-f", "-a", "v1", "-m", "v1")
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r.returncode == 1 and "기준 corpus 찾기 실패(corpus_missing)" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V


# ------------------------------------------------------------------ U-2 check_binding: PostgreSQL store가 있으면 DB 대조(D-323 Q-1)
def _pg_config(tmp_path, repo, env_file):
    root, conf = _config(tmp_path, repo)
    cfg = yaml.safe_load(conf.read_text())
    cfg["execution"]["stores"]["history"] = {"backend": "pgvector", "release": BASE_V, "connection": {"env": "LLMOPSAGENT_RUNTIME_DSN"},
                                             "embedding": {"mode": "fake"}}
    cfg["runtime_db"] = {"env_file": str(env_file)}
    conf.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return root, conf


@needs_index
def test_u2_pg_store_without_dsn_stops(tmp_path, monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    root, conf = _pg_config(tmp_path, _pack_repo(tmp_path), tmp_path / "없는.env")
    r = _run_script(root, "check", "--config", str(conf))
    assert r.returncode == 1 and "묶기 대조(DB) 못 함" in r.stdout and "LLMOPSAGENT_RUNTIME_DSN" in r.stdout, r.stdout + r.stderr


@needs_index
def test_u2_pg_store_unreachable_db_stops_without_the_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    env_file = tmp_path / "deploy.env"
    env_file.write_text("LLMOPSAGENT_RUNTIME_DSN=postgresql://runtime_reader:s3cret-pw-u2@127.0.0.1:1/x?connect_timeout=2\n")
    root, conf = _pg_config(tmp_path, _pack_repo(tmp_path), env_file)
    r = _run_script(root, "check", "--config", str(conf))
    assert r.returncode == 1 and "묶기" in r.stdout and "DB" in r.stdout, r.stdout + r.stderr   # packrt pg 대조까지 감(접속 실패)
    assert "s3cret-pw-u2" not in r.stdout + r.stderr


def test_u2_source_passes_the_dsn_only_as_an_env_argument():
    """DSN은 packrt에 env= 인자로만(os.environ에 넣지 않음) · 파일 store만이면 pg_lookup=False 그대로."""
    src = (ROOT / "scripts" / "r12-update").read_text(encoding="utf-8")
    assert "load_execution_config(cfg, load_pack(copy.pack_dir).doc, env=env, pg_lookup=s.uses_postgres)" in src
    assert "os.environ[RUNTIME_DSN_ENV]" not in src


# ------------------------------------------------------------------ U-3 R12 load 단계(pgvector store만, D-323 Q-3)
@needs_index
def test_u3_pg_store_without_loader_env_file_stops_after_build(tmp_path):
    root, conf = _pg_config(tmp_path, _pack_repo(tmp_path), tmp_path / "deploy.env")
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r.returncode == 1 and "--loader-env-file" in r.stdout and "build 뒤 멈춤" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V   # 운영 설정 그대로
    h = _state(tmp_path)["history"][-1]
    assert h["status"] == "failed" and "--loader-env-file" in h["reason"] and _state(tmp_path)["done_reports"] == []
    assert any((tmp_path / "data" / "releases" / "indexes").iterdir())   # build는 끝남(폴더는 지우지 않음)


@needs_index
def test_u3_loader_env_file_without_the_name_stops(tmp_path):
    root, conf = _pg_config(tmp_path, _pack_repo(tmp_path), tmp_path / "deploy.env")
    _inbox(tmp_path)
    lf = tmp_path / "loader.env"
    lf.write_text("OTHER=1\n")
    r = _run_script(root, "update", "--config", str(conf), *UPDATE, "--loader-env-file", str(lf))
    assert r.returncode == 1 and "LLMOPSAGENT_INDEX_LOADER_DSN 줄이 없습니다" in r.stdout, r.stdout + r.stderr
    assert _live_release(root, conf) == BASE_V


def test_u3_children_get_no_db_secret_and_load_gets_only_the_loader_dsn(monkeypatch):
    """corpus · build · load --check 자식 env에는 …_DSN · …PASSWORD 이름 0, load 자식에만 LLMOPSAGENT_INDEX_LOADER_DSN · 실패 글의 비밀은 가림."""
    import importlib.machinery
    import importlib.util
    ld = importlib.machinery.SourceFileLoader("r12_u3", str(ROOT / "scripts" / "r12-update"))
    spec = importlib.util.spec_from_loader("r12_u3", ld)
    m = importlib.util.module_from_spec(spec)
    ld.exec_module(m)
    seen = []

    def fake_run(argv, capture_output, text, env):
        seen.append(env)
        st = {"build": "built", "load": "loaded"}[argv[3]]
        if "--check" in argv:
            st = "checked"
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": st}) + "\n", "")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    monkeypatch.setenv("LLMOPSAGENT_RUNTIME_DSN", "postgresql://r:pw1@h/x")
    monkeypatch.setenv("LLMOPSAGENT_TRACE_DSN", "postgresql://t:pw2@h/x")
    monkeypatch.setenv("DB_OWNER_PASSWORD", "pw3")
    m.packrt_cli("build", "--pack", "p")
    m.packrt_cli("load", "--pack", "p", "--release", "r", "--check", ok=("checked",))
    m.packrt_cli("load", "--pack", "p", "--release", "r", ok=("loaded", "exists"), extra_env={m.LOADER_DSN_ENV: "postgresql://index_loader:pw4@h/x"})
    for env in seen:
        assert not [k for k in env if (k.endswith("_DSN") and k != m.LOADER_DSN_ENV) or "PASSWORD" in k]
    assert [m.LOADER_DSN_ENV in env for env in seen] == [False, False, True]

    def failing(argv, capture_output, text, env):
        return subprocess.CompletedProcess(argv, 1, json.dumps({"status": "failed", "error": {"code": "load_failed", "message": "x pw4-secret y"}}), "")
    monkeypatch.setattr(m.subprocess, "run", failing)
    with pytest.raises(m.Stop) as e:
        m.packrt_cli("load", "--pack", "p", "--release", "r", ok=("loaded", "exists"), hide=["pw4-secret"])
    assert "load_failed" in str(e.value) and "pw4-secret" not in str(e.value)


# ------------------------------------------------------------------ W-3 되돌림(VPS 호스트가 다시 띄운 서버가 맞지 않을 때, D-325)
@needs_index
def test_w3_revert_restores_the_config_before_r12_and_reopens_the_reports(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)
    assert r.returncode == 0, r.stdout + r.stderr
    live = root / "config-live" / conf.name
    before = live.parent / f".{live.name}.before-r12"
    assert _live_release(root, conf) != BASE_V and yaml.safe_load(before.read_text())["execution"]["stores"]["history"]["release"] == BASE_V
    r = _run_script(root, "revert", "--config", str(conf), "--reason", "시험: drs 다름")
    assert r.returncode == 0 and "운영 설정 되돌림" in r.stdout, r.stdout + r.stderr
    st = _state(tmp_path)
    assert _live_release(root, conf) == BASE_V and not before.exists()
    assert st["done_reports"] == [] and st["history"][-1]["status"] == "reverted" and st["history"][-1]["reports"] == ["RPT-0001"]
    again = _run_script(root, "revert", "--config", str(conf))
    assert again.returncode == 1 and "되돌릴 옛 설정이 없습니다" in again.stdout
    r = _run_script(root, "update", "--config", str(conf), *UPDATE)   # 다시 부르면 같은 보고서를 다시 처리(같은 묶음 = 같은 corpus id)
    assert r.returncode == 0 and "corpus exists" in r.stdout and _live_release(root, conf) != BASE_V, r.stdout + r.stderr


# ------------------------------------------------------------------ G-1 · G-2 장부 증거 세 칸 · 묶기 대조가 장부 앞 · 되돌림 알림(D-326 · D-328)
@needs_index
def test_g1_evidence_has_three_more_fields_from_packrt(tmp_path):
    from packrt.ir import load_pack
    from packrt.ir.releases import load_execution_config
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    got = tmp_path / "ev-seen.json"
    r = _run_script(root, "update", "--config", str(conf), *UPDATE, "--ledger-cmd", f"cp {{evidence}} {got}")
    assert r.returncode == 0, r.stdout + r.stderr
    ev = json.loads(got.read_text())
    assert set(ev) == {"kind", "reports", "store", "previous", "version", "corpus", "drs", "manifest_sha256", "config_sha256", "model_release_id"}
    live = root / "config-live" / conf.name
    new_v = yaml.safe_load(live.read_text())["execution"]["stores"]["history"]["release"]
    assert ev["version"] == new_v and ev["model_release_id"] is None   # 시험 Pack은 모델 없음
    data = tmp_path / "data"   # 서버가 쓸 실행 설정을 packrt로 다시 읽어 같은 값인지(llmops admin.registration과 같은 계산)
    from deploy_server.settings import load_settings
    from deploy_server.pack_source import materialize, write_execution_config
    s = load_settings(live)
    copy = materialize(s.pack_repo, s.pack_ref, s.packs_dir, s.pack_subdir, s.releases_subdir)
    cfg = load_execution_config(write_execution_config(data / "g1.yaml", copy, s.execution, s.release_roots_extra), load_pack(copy.pack_dir).doc,
                                pg_lookup=False)
    assert ev["config_sha256"] == cfg["config_sha256"] and ev["manifest_sha256"] == cfg["bindings"]["stores"]["history"]["manifest_sha256"]
    assert _state(tmp_path)["history"][-1]["ledger_drs"] == ev["drs"]


@needs_index
def test_g1_binding_failure_does_not_call_the_ledger(tmp_path, monkeypatch):
    """G-1 순서: 묶기 대조가 장부 요청 앞 — 대조가 멈추면 장부 명령은 불리지 않고 운영 설정 그대로(장부에 돌지 않을 줄 0)."""
    import argparse
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    m = _r12_module()
    monkeypatch.setattr(m, "ROOT", root)

    def stop_binding(live, text):
        raise m.Stop("packrt 묶기 실패 — 시험")
    monkeypatch.setattr(m, "check_binding", stop_binding)
    mark = tmp_path / "ledger-called"
    a = argparse.Namespace(config=str(conf), store="history", graph="g", node="find_similar", ledger_cmd=f"touch {mark}", port=8799,
                           loader_env_file=None)
    with pytest.raises(m.Stop, match="묶기 실패"):
        m.update(a)
    assert not mark.exists() and _live_release(root, conf) == BASE_V
    h = _state(tmp_path)["history"][-1]
    assert h["status"] == "failed" and "ledger_drs" not in h


@needs_index
def test_g2_revert_after_a_ledger_line_warns_that_it_is_unused(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE, "--ledger-cmd", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    drs = _state(tmp_path)["history"][-1]["drs"]
    r = _run_script(root, "revert", "--config", str(conf))
    assert r.returncode == 0 and f"장부에 쓴 R12 줄(drs {drs})이 돌지 않음" in r.stdout, r.stdout
    assert _state(tmp_path)["history"][-1]["ledger_line_unused"] == drs


# ------------------------------------------------------------------ G-5 장부 앞뒤 두 단계(VPS — 호스트가 llmops 컨테이너에서 장부, D-328 Q-2 (가))
@needs_index
def test_g5_until_ledger_then_after_or_abort(tmp_path):
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    _inbox(tmp_path)
    r = _run_script(root, "update", "--config", str(conf), *UPDATE, "--until-ledger")
    assert r.returncode == 0 and "장부 요청 대기" in r.stdout, r.stdout + r.stderr
    pend = json.loads(next(l for l in r.stdout.splitlines() if l.startswith("R12 대기: "))[len("R12 대기: "):])
    assert pend["deployment_id"] == "r12@dev-local" and set(pend["evidence"]) >= {"manifest_sha256", "config_sha256", "model_release_id", "drs"}
    assert _live_release(root, conf) == BASE_V and not (tmp_path / "data" / "r12-state.json").exists()   # 운영 설정 · 기록 그대로
    r = _run_script(root, "abort-ledger", "--config", str(conf), "--reason", "시험: llmops 거절")
    assert r.returncode == 0 and _live_release(root, conf) == BASE_V
    st = _state(tmp_path)
    assert st["done_reports"] == [] and st["history"][-1]["status"] == "failed" and "llmops 거절" in st["history"][-1]["reason"]
    r = _run_script(root, "update", "--config", str(conf), *UPDATE, "--until-ledger")   # 다시: 같은 묶음 = 같은 corpus id(exists)
    assert r.returncode == 0 and "corpus exists" in r.stdout, r.stdout
    r = _run_script(root, "after-ledger", "--config", str(conf))
    assert r.returncode == 0 and "운영 설정 바꿈" in r.stdout, r.stdout + r.stderr
    st = _state(tmp_path)
    assert st["done_reports"] == ["RPT-0001"] and st["history"][-1]["status"] == "restart_needed" and st["history"][-1]["ledger_drs"] == pend["drs"]
    assert _live_release(root, conf) == pend["evidence"]["version"] and not (tmp_path / "data" / "r12-pending.json").exists()
    again = _run_script(root, "after-ledger", "--config", str(conf))
    assert again.returncode == 1 and "장부 요청 대기가 없습니다" in again.stdout
