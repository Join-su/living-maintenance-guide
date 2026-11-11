"""kg_apply 예외 관문(D-554 ① · dev-2) 가짜 시험 — 관문 · 추출기 · 적재기 · 서버 모두 가짜, 네트워크 · DB 0.
관문(KG_GATE)은 이 시험이 만든 작은 대본입니다: 차례마다 미리 정한 판정을 내고, 받은 입력 파일을 남깁니다.
보는 것: 통과면 배포 · 실패 + KG 후보면 고쳐 다시(최대 3번) · 그래도 실패 · 후보 없음 · 관문 못 돎이면 보류(배포 0 · 그래프 그대로)
· 신호 파일(KG_SIGNAL_DIR) 꼴 kg-apply/status@1 · 보류 편지는 다시 처리 0 · 보류 때 쌓인 관계(nodes.json)는 그 전으로."""
import json
import sys
import textwrap

import pytest

from test_kg_apply import K, LETTER, letter  # noqa: F401 - fixture K 그대로(running · extract · load · new_revision · swap 가짜)

REL = {"id": "EX-R-0002", "mode": "안내", "records": ["R-0002"], "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 7.8]]},
       "where": "temp_diff_k<=7.8"}
OLD = {"id": "EX-R-0001", "mode": "안내", "records": ["R-0001"], "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 7.9]]},
       "where": "temp_diff_k<=7.9"}
NARROW = {"relation_id": "EX-R-0002", "change": "narrow", "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 7.6]]},
          "where": "temp_diff_k<=7.6", "why": "기존 시험 c1a15-start/007에 끼어듦"}
def narrow(x):
    """서로 다른 고침 후보(문턱 x) — 같은 고침은 두 번 쓰지 않으므로(D-561 ②) 고침 여러 번 시험은 후보가 달라야 함."""
    return {**NARROW, "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", x]]}, "where": f"temp_diff_k<={x}"}


FAIL = {"format": "kg-gate/result@1", "pass": False, "checks": {"no_break": {"pass": False, "broken": ["c1a15-start/007"]}}, "reason": "no_break"}
OK = {"format": "kg-gate/result@1", "pass": True, "checks": {"no_break": {"pass": True}}, "reason": None}


@pytest.fixture
def G(K, monkeypatch, tmp_path):   # noqa: F811
    """관문 대본 · 신호 폴더 · 추출기가 새 관계 EX-R-0002를 nodes.json에 더하는 가짜."""
    gate = tmp_path / "gate.py"
    gate.write_text(textwrap.dedent(f"""
        import json, sys, pathlib
        inp = json.loads(pathlib.Path(sys.argv[sys.argv.index("--input") + 1]).read_text())
        d = pathlib.Path({str(tmp_path / "gate-seen")!r}); d.mkdir(exist_ok=True)
        (d / f"round-{{inp['round']}}.json").write_text(json.dumps(inp, ensure_ascii=False))
        plan = json.loads(pathlib.Path({str(tmp_path / "plan.json")!r}).read_text())
        step = plan[min(inp["round"], len(plan)) - 1]
        if step == "crash":
            print("관문 안에서 멈춤", file=sys.stderr); sys.exit(2)
        print("점검 중 …"); print(json.dumps(step, ensure_ascii=False))
    """))
    monkeypatch.setenv("KG_GATE", f"{sys.executable} -I -B {gate}")
    monkeypatch.setenv("KG_SIGNAL_DIR", str(tmp_path / "signal"))
    K.plan = lambda *steps: (tmp_path / "plan.json").write_text(json.dumps(list(steps), ensure_ascii=False))
    K.seen = lambda r: json.loads((tmp_path / "gate-seen" / f"round-{r}.json").read_text())
    K.sig = lambda rid="RPT-000001": json.loads((tmp_path / "signal" / f"{rid}.json").read_text())
    K.events = lambda: [json.loads(l) for l in (tmp_path / "signal" / "events.jsonl").read_text().splitlines()]
    K.bases = []

    def extract(new, tag):
        K.calls["extract"] += 1
        p = K.STATE / "nodes.json"
        acc = json.loads(p.read_text()) if p.exists() else {"sections": [], "exception_relations": []}
        acc["exception_relations"].append(dict(REL))
        p.write_text(json.dumps(acc, ensure_ascii=False))
        return 1

    def fake_load(tag=None, base_text=None):
        K.calls["load"] += 1
        K.bases.append(base_text)
        return {"tag": f"L{1 + K.calls['load']}", "release_id": "graph-x", "rejected": []}

    monkeypatch.setattr(K, "extract", extract)
    monkeypatch.setattr(K, "load", fake_load)
    monkeypatch.setattr(K, "new_revision", lambda tag_from, fixed=None, expect=None: K.calls.__setitem__("new_revision", K.calls["new_revision"] + 1) or "g2")
    monkeypatch.setattr(K.S, "pack_repo", lambda: tmp_path / "pack")
    monkeypatch.setattr(K, "git", lambda *a: json.dumps({"exception_relations": [OLD], "layer": "L1"}))
    return K


def rels(K):
    return json.loads((K.STATE / "nodes.json").read_text())["exception_relations"]


def test_pass_deploys_and_signals_applied(G):
    G.plan(OK)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "g2"
    assert G.calls["swap"] == ["g2"]
    inp = G.seen(1)
    assert inp["format"] == "kg-gate/input@1" and inp["round"] == 1 and inp["running_tag"] == "g1" and inp["new_relations"] == ["EX-R-0002"]
    assert inp["letters"][0]["report_id"] == "RPT-000001" and inp["letters"][0]["record_id"] == "R-0002"
    s = G.sig()
    assert s["format"] == "kg-apply/status@1" and s["status"] == "applied" and s["graph"] == {"running_tag": "g1", "new_tag": "g2"}
    assert s["rounds"] == 1 and s["fixed"] == []
    assert s["checks"] == OK["checks"]                        # 통과한 관문 결과(LLMOps 버전 표 '관문' 칸 · llmops-2 187)
    assert [e["status"] for e in G.events()] == ["checking", "applied"]


def test_fail_then_candidate_fixes_and_deploys(G):
    G.plan({**FAIL, "candidate": NARROW}, OK)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "g2"
    assert G.calls["load"] == 2 and G.calls["swap"] == ["g2"]
    assert rels(G)[0]["where"] == "temp_diff_k<=7.6" and rels(G)[0]["when"]["sensors"] == [["temp_diff_k", "<=", 7.6]]
    assert G.seen(2)["previous"][0]["applied_candidate"]["where"] == "temp_diff_k<=7.6"
    s = G.sig()
    assert s["status"] == "applied" and s["rounds"] == 2
    assert s["fixed"] == [{"relation_id": "EX-R-0002", "change": "narrow", "where": "temp_diff_k<=7.6"}]


def test_three_fixes_still_failing_holds(G):
    G.plan(*[{**FAIL, "candidate": narrow(x)} for x in (7.6, 7.5, 7.4, 7.3)])   # 매번 실패 + 다른 후보
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "held"
    assert G.calls["swap"] == [] and G.calls["new_revision"] == 0 and G.calls["load"] == 4   # 처음 + 고침 3번
    assert not (G.STATE / "nodes.json").exists()              # 보류 때 쌓인 관계는 그 전으로(이번 편지 전엔 없었음)
    assert (G.STATE / "done" / "RPT-000001").read_text().startswith("held")
    assert json.loads((G.STATE / "held" / "RPT-000001.json").read_text())["reason"] == "no_break"
    s = G.sig()
    assert s["status"] == "held" and s["rounds"] == 4 and s["graph"]["new_tag"] is None and len(s["fixed"]) == 3
    assert G.main() == "none"                                 # 보류 편지는 다시 처리 0
    assert G.calls["load"] == 4


def test_fail_without_candidate_holds_at_once(G):
    G.plan(FAIL)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "held"
    assert G.calls["load"] == 1 and G.calls["swap"] == []
    assert G.sig()["rounds"] == 1 and G.sig()["reason"] == "no_break"


def test_gate_crash_holds_with_gate_error(G):
    G.plan("crash")
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "held"
    assert G.calls["swap"] == [] and G.sig()["reason"] == "gate_error"


def test_bad_candidate_holds(G):
    G.plan({**FAIL, "candidate": {"relation_id": "EX-R-0002", "change": "narrow", "where": "temp_diff_k<=7.6"}})   # when 없음
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "held"
    assert G.sig()["reason"] == "bad_candidate" and G.calls["load"] == 1


def test_off_candidate_removes_relation_then_deploys(G):
    G.plan({**FAIL, "candidate": {"relation_id": "EX-R-0002", "change": "off", "why": "끼어듦이 너무 넓음"}}, OK)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "g2"
    assert rels(G) == [] and G.sig()["fixed"] == [{"relation_id": "EX-R-0002", "change": "off", "where": None}]


def test_candidate_on_running_relation_goes_through_base(G):
    G.plan({**FAIL, "candidate": {**NARROW, "relation_id": "EX-R-0001", "where": "temp_diff_k<=7.5",
                                  "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 7.5]]}}}, OK)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "g2"
    assert G.bases[0] is None                                 # 처음은 지금 도는 그래프 그대로
    base = json.loads(G.bases[1])
    assert base["exception_relations"][0]["id"] == "EX-R-0001" and base["exception_relations"][0]["where"] == "temp_diff_k<=7.5"


def test_unknown_relation_candidate_holds(G):
    G.plan({**FAIL, "candidate": {**NARROW, "relation_id": "EX-R-9999"}})
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "held" and G.sig()["reason"] == "bad_candidate"


def test_without_gate_old_path_still_swaps(G, monkeypatch):
    monkeypatch.delenv("KG_GATE")
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "g2" and G.calls["swap"] == ["g2"]
    assert G.sig()["status"] == "applied" and G.sig()["rounds"] == 0


def test_stop_before_gate_signals_failed(G, monkeypatch):
    def broken(new, tag):
        raise G.ApplyError("추출기 실패")
    monkeypatch.setattr(G, "extract", broken)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    with pytest.raises(SystemExit):
        G.main()
    assert G.sig()["status"] == "failed" and G.sig()["reason"] == "추출기 실패"
    assert not (G.STATE / "done" / "RPT-000001").exists()     # 실패는 done 없이 남음(지금과 같음)


def test_gate_revision_differs_holds_before_commit(G, monkeypatch):
    """Pack 관문 길(tools/graph_gate.py)이 낸 후보 revision과 배포가 셀 revision이 다르면 커밋 · 배포 0 → 보류 revision_mismatch."""
    G.plan({**OK, "cand_revision": "sha256:" + "9" * 64})

    def new_revision(tag_from, fixed=None, expect=None):
        if expect and expect != "sha256:" + "3" * 64:
            raise G.RevisionMismatch(expect, "sha256:" + "3" * 64)
        G.calls["new_revision"] += 1
        return "g2"
    monkeypatch.setattr(G, "new_revision", new_revision)
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "held"
    assert G.calls["new_revision"] == 0 and G.calls["swap"] == []
    assert G.sig()["reason"] == "revision_mismatch" and G.sig()["rounds"] == 1


def test_gate_revision_same_deploys(G, monkeypatch):
    G.plan({**OK, "cand_revision": "sha256:" + "3" * 64})
    seen = {}
    monkeypatch.setattr(G, "new_revision", lambda tag_from, fixed=None, expect=None: seen.setdefault("expect", expect) and "g2")
    letter(G, record={"record_id": "R-0002", "exception_record": {"situation": "s"}})
    assert G.main() == "g2" and seen["expect"] == "sha256:" + "3" * 64 and G.calls["swap"] == ["g2"]


def test_new_revision_compares_before_commit(K, monkeypatch, tmp_path):  # noqa: F811
    """진짜 new_revision: packrt 예상 revision이 expect와 다르면 커밋 · 태그 전에 멈추고 작업 트리를 되돌림."""
    import types
    real = __import__("test_kg_apply").load("kg_apply_real_rev", "kg_apply.py")
    monkeypatch.setenv("KG_STATE", str(tmp_path / "state"))
    real.STATE = tmp_path / "state"
    real.STATE.mkdir(exist_ok=True)
    (real.STATE / "current.json").write_text('{"layer": "L9"}')
    repo = tmp_path / "pack"
    (repo / "pack" / "templates" / "graph").mkdir(parents=True)
    monkeypatch.setattr(real.S, "pack_repo", lambda: repo)
    calls = []
    monkeypatch.setattr(real, "git", lambda *a: calls.append(a) or "")
    fake = types.ModuleType("packrt.ir.patch")
    fake.apply_patch = lambda root, ops, out_dir: (out_dir.joinpath(real.S.GRAPH).parent.mkdir(parents=True, exist_ok=True),
                                                    out_dir.joinpath(real.S.GRAPH).write_text("{}"),
                                                    types.SimpleNamespace(errors=[], revision="sha256:" + "3" * 64))[-1]
    monkeypatch.setitem(sys.modules, "packrt.ir.patch", fake)
    with pytest.raises(real.RevisionMismatch):
        real.new_revision("g1", expect="sha256:" + "9" * 64)
    assert not any(a[0] in ("commit", "tag") or "commit" in a for a in calls)
    assert ("checkout", "-q", "--", ".") in calls
