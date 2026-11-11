"""체크 기록 → 현장 정답 파일 놓기(D-470 덧 5 · 6, VFE EV-005 FIELD-GOLD-E1).
서버: 기록기가 새로 쓴 체크 기록 201 뒤에만 일감 파일 하나(설정 field_gold 있을 때만 · 응답 같음).
일꾼(scripts/field-gold.py): CLI sha 대조 → 가짜 CLI → gate.ok true · expected 있을 때만 놓음 · 같으면 그대로 · 다르면 충돌 파일만.
가짜 CLI · 가짜 기록기 — 네트워크 · DB · 실제 호출 0."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from deploy_server.settings import SettingsError, load_settings
from test_check_record import CheckWriter, record
from test_feedback_api import _secrets, fb, post_run

ROOT = Path(__file__).resolve().parents[1]

# 가짜 CLI: --record 문서의 record_key와 env 없이 같은 폴더의 mode.json으로 결과를 정함(진짜 CLI와 같은 인자 · 종료 코드 꼴)
FAKE_CLI = r'''
import argparse, json, pathlib, sys
ap = argparse.ArgumentParser()
for a in ("--record", "--history-release", "--manual-release", "--history-corpus", "--out"):
    ap.add_argument(a, required=True)
a = ap.parse_args()
mode = json.loads((pathlib.Path(__file__).parent / "mode.json").read_text())
rec = json.loads(pathlib.Path(a.record).read_text())
rk = rec["record_key"]
(pathlib.Path(__file__).parent / "calls.txt").open("a").write(" ".join(sys.argv[1:]) + "\n")
if mode["exit"] == 2:
    print("[현장 정답] 오류: 증상군 모름", file=sys.stderr); sys.exit(2)
if mode["exit"] != 0:
    raise RuntimeError("가짜 예외")
ok = mode["gate"]
out = {"schema": "c1a15.field-gold.v1", "case_id": "F-" + rk, "source": "field", "record_key": rk, "signals": ["S1"],
       "provenance": {"start_run_id": rec["runs"]["start"]},
       "gate": {"ok": True} if ok else {"ok": False, "failed": ["G5"]},
       "confirmed": {"type": "원인 확정", "code": mode.get("code", "CL-1")} if ok else None,
       "expected": {"nodes": {"retrieve_manual": [{"evidence": [{"store": "manuals", "locator": mode.get("locator", "4.3"), "rule": "r"}]}]}} if ok else None,
       "negatives": []}
pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False))
print("[현장 정답] F-" + rk + (" · 문 통과" if ok else " · 문 막힘"))
'''


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def cli(tmp_path):
    d = tmp_path / "eval"
    d.mkdir()
    (d / "field_gold.py").write_text(FAKE_CLI)
    (d / "make_gold.py").write_text("# 가짜 make_gold\n")
    (d / "history.jsonl").write_text('{"id": "H-001"}\n')
    (d / "mode.json").write_text(json.dumps({"exit": 0, "gate": True}))
    return d


def fg_conf(tmp_path, cli, **over):
    c = {"out_dir": str(tmp_path / "gold-out"), "cli": str(cli / "field_gold.py"), "history_corpus": str(cli / "history.jsonl"),
         "sha256": {"field_gold": sha(cli / "field_gold.py"), "make_gold": sha(cli / "make_gold.py"), "history_corpus": sha(cli / "history.jsonl")}}
    c.update(over)
    return c


@pytest.fixture
def srv(make_deployment, client_for, cases, tmp_path):
    def make(field_gold=None, writer=None):
        w = writer or CheckWriter(tmp_path / "trace", secrets=_secrets)
        dep = make_deployment(trace_writer=w, **({"field_gold": field_gold} if field_gold else {}))
        c = client_for(dep)
        run = post_run(c, cases["C01"], "fg-c01").json()["run_id"]
        return dep, w, c, run
    return make


def outbox(dep):
    d = dep.settings.data_dir / "field-gold" / "outbox"
    return sorted(p.name for p in d.glob("*.json")) if d.exists() else []


# ── 설정 ──

def test_settings_field_gold_optional_and_checked(tmp_path, cli):
    base = ROOT / "config" / "dev-local.yaml"
    assert load_settings(base).field_gold is None
    s = load_settings(base, overrides={"field_gold": fg_conf(tmp_path, cli)})
    assert s.field_gold["cli"] == cli / "field_gold.py" and s.field_gold["out_dir"] == tmp_path / "gold-out"
    with pytest.raises(SettingsError, match="field_gold"):
        load_settings(base, overrides={"field_gold": {"out_dir": "x", "cli": "y"}})
    with pytest.raises(SettingsError, match="sha256"):
        load_settings(base, overrides={"field_gold": fg_conf(tmp_path, cli, sha256={"field_gold": "abc", "make_gold": "d" * 64, "history_corpus": "e" * 64})})


# ── 서버: 일감 파일 ──

def test_server_writes_job_after_new_check_record(srv, tmp_path, cli):
    dep, w, c, run = srv(field_gold=fg_conf(tmp_path, cli))
    body = record(dep, run)
    r = fb(c, run, body, "CR-000001")
    assert r.status_code == 201
    assert set(r.json()) == {"feedback_id", "run_id", "at", "kind", "record_key", "record_ref"}   # 응답 바뀜 0
    assert outbox(dep) == ["CR-000001.json"]
    job = json.loads((dep.settings.data_dir / "field-gold" / "outbox" / "CR-000001.json").read_text())
    assert job["record_key"] == "CR-000001" and job["run_id"] == run and job["start_run_id"] == run
    assert job["record"] == body["record"] and job["data_release_id"] == dep.runner.data_release_id
    assert job["release_match"] is True
    for name, st in job["stores"].items():
        assert st["release_id"] and (st["dir"] is None or Path(st["dir"]).is_dir())


def test_server_no_job_on_replay_duplicate_or_without_setting(srv, tmp_path, cli):
    dep, w, c, run = srv(field_gold=fg_conf(tmp_path, cli))
    body = record(dep, run)
    fb(c, run, body, "CR-000001")
    job = dep.settings.data_dir / "field-gold" / "outbox" / "CR-000001.json"
    before = job.stat().st_mtime_ns
    job.unlink()
    assert fb(c, run, body, "CR-000001").headers.get("Idempotent-Replayed") == "true"   # 멱등 재응답 → 일감 0
    assert outbox(dep) == []
    w.record_check_record = lambda **kw: {"result": "duplicate", "record_ref": "sha256:" + "d" * 64, "body_sha256": "x"}
    assert fb(c, run, record(dep, run, record_key="CR-000002"), "CR-000002").status_code == 201   # 기록기 duplicate → 일감 0
    assert outbox(dep) == [] and before

    dep2, _, c2, run2 = srv()
    assert fb(c2, run2, record(dep2, run2, record_key="CR-000009"), "CR-000009").status_code == 201
    assert dep2.settings.field_gold is None and "CR-000009.json" not in outbox(dep2)   # 설정 없으면 지금과 같음(일감 0)


# ── 일꾼 ──

def load_worker():
    spec = importlib.util.spec_from_file_location("field_gold_worker", ROOT / "scripts" / "field-gold.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def W(srv, tmp_path, cli):
    """서버로 진짜 일감을 하나 만들고, 일꾼을 그 설정으로 묶음."""
    dep, w, c, run = srv(field_gold=fg_conf(tmp_path, cli))
    hist, man = tmp_path / "rel-h", tmp_path / "rel-m"
    for d in (hist, man):
        d.mkdir()
        (d / "chunks.jsonl").write_text("{}\n")
    m = load_worker()
    m.P = m.paths(dep.settings)

    def job(rk="CR-000001", **over):
        body = record(dep, run, record_key=rk)
        assert fb(c, run, body, rk).status_code == 201
        p = m.P["outbox"] / f"{rk}.json"
        j = json.loads(p.read_text())
        j["stores"] = {"history": {"release_id": "ir-history-x", "dir": str(hist)}, "manuals": {"release_id": "ir-manuals-y", "dir": str(man)}}
        j.update(over)
        p.write_text(json.dumps(j, ensure_ascii=False))
        return p
    m.job, m.cli, m.dep = job, cli, dep
    return m


def mode(cli, **kw):
    (cli / "mode.json").write_text(json.dumps({"exit": 0, "gate": True, **kw}))


def state(W, rk="CR-000001"):
    return json.loads((W.P["state"] / f"{rk}.json").read_text())


def test_gate_ok_places_file(W):
    W.process(W.job())
    out = W.P["out"] / "CR-000001.json"
    d = json.loads(out.read_text())
    assert d["gate"]["ok"] is True and d["record_key"] == "CR-000001"
    assert state(W)["stage"] == "placed"
    assert not (W.P["outbox"] / "CR-000001.json").exists() and (W.P["outbox"] / "done" / "CR-000001.json").exists()
    call = (W.cli / "calls.txt").read_text()
    assert "--history-release" in call and "rel-h" in call and "rel-m" in call and str(W.P["out"]) not in call   # CLI는 작업 폴더에 씀
    assert f"--history-corpus {W.cli / 'history.jsonl'}" in call


def test_gate_blocked_not_placed(W):
    mode(W.cli, gate=False)
    W.process(W.job())
    assert not (W.P["out"] / "CR-000001.json").exists()
    st = state(W)
    assert st["stage"] == "gate_blocked" and st["failed"] == ["G5"]


def test_same_output_kept_different_marks_conflict(W):
    W.process(W.job())
    out = W.P["out"] / "CR-000001.json"
    first = out.read_bytes()
    W.retry("CR-000001")
    W.process(W.P["outbox"] / "CR-000001.json")
    assert out.read_bytes() == first and state(W)["stage"] == "same"
    assert not (W.P["out"] / "CR-000001.conflict.json").exists()
    mode(W.cli, locator="4.5")                       # 다시 결정 → 다른 정답
    W.retry("CR-000001")
    W.process(W.P["outbox"] / "CR-000001.json")
    assert out.read_bytes() == first                 # 덮어쓰기 0
    cf = json.loads((W.P["out"] / "CR-000001.conflict.json").read_text())
    assert cf["record_key"] == "CR-000001" and cf["kept_sha256"] != cf["new_sha256"]
    assert cf["new"]["expected"]["nodes"]["retrieve_manual"][0]["evidence"][0]["locator"] == "4.5"
    assert state(W)["stage"] == "conflict"


def test_input_error_and_crash_not_placed(W):
    mode(W.cli, exit=2)
    W.process(W.job())
    st = state(W)
    assert st["stage"] == "input_error" and st["exit"] == 2 and st["stderr"].startswith("[현장 정답] 오류")
    mode(W.cli, exit=1)
    W.process(W.job("CR-000002"))
    assert state(W, "CR-000002")["stage"] == "crash"
    assert list(W.P["out"].glob("*.json")) == [] if W.P["out"].exists() else True


@pytest.mark.parametrize("name", ["field_gold.py", "make_gold.py", "history.jsonl"])
def test_cli_or_corpus_sha_mismatch_refused_without_running(W, name):
    p = W.job()
    (W.cli / name).write_text("# 바뀜\n")
    W.process(p)
    st = state(W)
    assert st["stage"] == "tool_sha_mismatch" and name.split(".")[0].replace("history", "history_corpus") in st["which"]
    assert not (W.cli / "calls.txt").exists()
    assert not (W.P["out"] / "CR-000001.json").exists()


def test_corpus_missing_refused(W):
    p = W.job()
    (W.cli / "history.jsonl").unlink()
    W.process(p)
    assert state(W)["stage"] == "tool_sha_mismatch" and "history_corpus" in state(W)["which"]


def test_record_key_file_name_rule(W):
    """리드 Q2 답: LLMOps는 [A-Za-z0-9._-] 1 ~ 128자만 파일 이름으로 읽음 → 놓는 쪽도 같은 규칙(벗어나면 안 놓음)."""
    assert W.name_ok("CR-10292341") and W.name_ok("a" * 128) and W.name_ok("x.y_z-1")
    assert not W.name_ok("") and not W.name_ok("a" * 129) and not W.name_ok("CR/1") and not W.name_ok("..") and not W.name_ok("CR 1")


def test_release_unknown(W):
    W.process(W.job(release_match=False))
    assert state(W)["stage"] == "release_unknown"
    assert not (W.cli / "calls.txt").exists()


def test_worker_refuses_without_setting(tmp_path):
    m = load_worker()
    s = load_settings(ROOT / "config" / "dev-local.yaml")
    with pytest.raises(SystemExit, match="field_gold"):
        m.paths(s)
