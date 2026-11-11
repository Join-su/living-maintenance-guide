"""kg_apply가 부르는 꼴 — Pack 약속 v9(D-556 · dev-2) 가짜 시험, 네트워크 · DB · 실제 호출 0.
보는 것: 예외 칸 items(AI 나눔 · 점검 1 + 조치 1 × n) 중 정비자 '아님'(new_exception)만 추출기 · 관문에 감 · '같음'(missed)만이면 추출 0
· item 꼴이 틀리면 추출기 부르기 전에 멈춤(done 없이 남음 · 신호 failed) · 1단계(KG_GATE_FIXES=0) = 관문 한 번 · 실패면 고침 0 · 바로 보류."""
import json
import types
from pathlib import Path

import pytest

from test_kg_apply import K, LETTER, letter, load  # noqa: F401 - fixture K
from test_kg_gate import FAIL, G, NARROW, OK, narrow  # noqa: F401 - fixture G

ITEM = [{"check": {"what": "냉각수 유량", "how": "유량계 눈금 읽기", "criteria": "12 L/min 이상 정상", "minutes": 5},
         "action": {"what": "펌프 임펠러 교체", "parts": ["임펠러"], "minutes": None}},
        {"check": {"what": "열교환기 막힘", "how": "입출구 온도 차 재기", "criteria": "차 8 K 이상 정상", "minutes": None},
         "action": {"what": "열교환기 세척", "parts": [], "minutes": 30}},
        {"check": {"what": "팬 회전", "how": "회전계로 재기", "criteria": "1450 rpm 이상 정상", "minutes": 3},
         "action": {"what": "팬 벨트 조임", "parts": [], "minutes": 10}}]


def v9(review=None, items=ITEM):
    er = {"situation": "s", "items": items, "result": "재가동 정상", "note": None}
    if review is not None:
        er["exception_review"] = review
    return {**LETTER, "record": {"record_id": "R-0002", "session_id": "S-1", "human_check": {"section": "EX-NEW", "from_section": "3.2"},
                                 "exception_record": er}}


@pytest.fixture
def R(K, monkeypatch, tmp_path):  # noqa: F811
    """진짜 extract · 가짜 추출기(받은 records 파일을 남김)."""
    real = load("kg_apply_real_v9", "kg_apply.py")
    monkeypatch.setenv("KG_ANSWERS", str(tmp_path / "a.json"))
    monkeypatch.setenv("KG_EXGRAPH", str(tmp_path / "exgraph.py"))
    monkeypatch.setattr(real, "git", lambda *a: "{}")
    real.seen = []

    def sh(args, **kw):
        real.seen.append(json.loads(Path(args[args.index("--records") + 1]).read_text()))
        Path(args[args.index("--out") + 1]).write_text(json.dumps({"sections": [], "exception_relations": [{"id": "EX-R-0002"}]}))
        return types.SimpleNamespace(returncode=0, stdout='{"added": ["R-0002"]}', stderr="")
    monkeypatch.setattr(real.A, "sh", sh)
    real.STATE.mkdir(parents=True, exist_ok=True)
    return real


def test_only_not_items_go_to_extractor(R):
    assert R.extract([(Path("x.json"), v9([{"item": 0, "decision": "same", "match": {"kind": "예외", "no": "EX-R-0001"}},
                                            {"item": 1, "decision": "not", "match": None}, {"item": 2, "decision": "not", "match": None}]))], "g1") == 1
    er = R.seen[0]["items"][0]["exception_record"]
    assert er["kind"] == "new_exception" and er["items"] == ITEM[1:] and er["item_index"] == [1, 2]
    assert "exception_review" not in er and er["situation"] == "s" and er["result"] == "재가동 정상"


def test_without_review_all_items_are_new(R):
    R.extract([(Path("x.json"), v9())], "g1")
    assert R.seen[0]["items"][0]["exception_record"]["items"] == ITEM


def test_all_same_means_no_extraction(R):
    assert R.extract([(Path("x.json"), v9([{"item": i, "decision": "same", "match": None} for i in range(3)]))], "g1") == 0
    assert R.seen == []


@pytest.mark.parametrize("bad,where", [({"what": ""}, "check.what"), ({"how": 3}, "check.how"), ({"minutes": 0}, "check.minutes"), ({"minutes": 2.5}, "check.minutes")])
def test_bad_item_stops_before_extractor(R, bad, where):
    items = [ITEM[0], {**ITEM[1], "check": {**ITEM[1]["check"], **bad}}]
    with pytest.raises(R.ApplyError, match=f"item 1 {where}"):
        R.extract([(Path("x.json"), v9(items=items))], "g1")
    assert R.seen == []


def test_bad_item_in_same_item_is_not_checked(R):
    """'같음'으로 고른 item은 그래프에 가지 않으므로 꼴을 보지 않음."""
    items = [{**ITEM[0], "action": {"what": "", "parts": [], "minutes": None}}, ITEM[1]]
    R.extract([(Path("x.json"), v9([{"item": 0, "decision": "same", "match": None}, {"item": 1, "decision": "not", "match": None}], items))], "g1")
    assert R.seen[0]["items"][0]["exception_record"]["items"] == [ITEM[1]]


@pytest.mark.parametrize("review", [[{"item": 3, "decision": "not"}], [{"item": 0, "decision": "maybe"}], "not"])
def test_bad_review_stops(R, review):
    with pytest.raises(R.ApplyError, match="exception_review"):
        R.extract([(Path("x.json"), v9(review))], "g1")


def test_old_shape_letter_unchanged(R):
    R.extract([(Path("x.json"), LETTER)], "g1")
    assert R.seen[0]["items"][0]["exception_record"] == LETTER["record"]["exception_record"]


def test_gate_input_carries_new_exception_items(G):  # noqa: F811
    G.plan(OK)
    l = v9([{"item": 0, "decision": "same", "match": None}, {"item": 1, "decision": "not", "match": None}])
    letter(G, record=l["record"])
    assert G.main() == "g2"
    x = G.seen(1)["letters"][0]
    assert x["exception_record"]["kind"] == "new_exception" and x["exception_record"]["items"] == [ITEM[1]]
    assert x["session_id"] == "S-1" and x["human_check"] == {"section": "EX-NEW", "from_section": "3.2"}


def test_stage1_fixes_zero_holds_without_fix(G, monkeypatch):  # noqa: F811
    monkeypatch.setenv("KG_GATE_FIXES", "0")
    G.plan({**FAIL, "candidate": NARROW}, OK)                 # 후보가 와도 1단계는 고치지 않음
    letter(G, record=v9()["record"])
    assert G.main() == "held"
    assert G.seen(1)["max_rounds"] == 1 and G.calls["load"] == 1 and G.calls["swap"] == [] and G.calls["new_revision"] == 0
    s = G.sig()
    assert s["status"] == "held" and s["rounds"] == 1 and s["fixed"] == [] and s["reason"] == "no_break"
    assert [e["status"] for e in G.events()] == ["checking", "held"]


def test_stage1_pass_deploys(G, monkeypatch):  # noqa: F811
    monkeypatch.setenv("KG_GATE_FIXES", "0")
    G.plan(OK)
    letter(G, record=v9()["record"])
    assert G.main() == "g2" and G.sig()["status"] == "applied" and G.sig()["rounds"] == 1


# D-558 6: 관문은 새 예외가 든 승인 건만 · 나누지 못한(items 0) 새 예외는 관문 0 · 바로 보류 + 알림
HISTORY = {"record_id": "H-0002"}                                     # 일반 이력만(예외 칸 없음)


def not_split():
    l = v9(items=[])
    l["record"] = {**l["record"], "record_id": "R-0003"}
    return l["record"]


def test_history_only_letter_skips_gate(G):  # noqa: F811
    G.plan("crash")                                                   # 관문을 부르면 보류가 됨 — 부르지 않아야 함
    letter(G, record=HISTORY)
    assert G.main() == "g2" and G.calls["swap"] == ["g2"]
    assert not (G.STATE / "gate").exists()
    s = G.sig()
    assert s["status"] == "applied" and s["rounds"] == 0
    assert [e["status"] for e in G.events()] == ["applied"]


def test_all_same_items_skip_gate(G):  # noqa: F811
    G.plan("crash")
    letter(G, record=v9([{"item": i, "decision": "same", "match": None} for i in range(3)])["record"])
    assert G.main() == "g2" and not (G.STATE / "gate").exists()


def test_exception_letter_still_goes_through_gate(G):  # noqa: F811
    G.plan(OK)
    letter(G, "RPT-000001", record=HISTORY)
    letter(G, "RPT-000002", record=v9()["record"])
    assert G.main() == "g2" and G.seen(1)["round"] == 1


def test_not_split_exception_holds_without_gate(G):  # noqa: F811
    G.plan("crash")
    letter(G, record=not_split())
    assert G.main() == "held"
    assert G.calls["extract"] == 0 and G.calls["load"] == 0 and G.calls["swap"] == [] and not (G.STATE / "gate").exists()
    assert (G.STATE / "done" / "RPT-000001").read_text().startswith("held")
    assert json.loads((G.STATE / "held" / "RPT-000001.json").read_text())["reason"] == "not_split"
    s = G.sig()
    assert s["status"] == "held" and s["reason"] == "not_split" and s["rounds"] == 0 and s["graph"] == {"running_tag": "g1", "new_tag": None}
    assert G.main() == "none"


def test_not_split_held_and_history_letter_still_applied(G):  # noqa: F811
    G.plan("crash")
    letter(G, "RPT-000001", record=not_split())
    letter(G, "RPT-000002", record=HISTORY)
    assert G.main() == "g2" and G.calls["swap"] == ["g2"] and not (G.STATE / "gate").exists()
    assert G.sig("RPT-000001")["status"] == "held" and G.sig("RPT-000001")["reason"] == "not_split"
    assert G.sig("RPT-000002")["status"] == "applied"
    assert (G.STATE / "done" / "RPT-000001").read_text().startswith("held") and (G.STATE / "done" / "RPT-000002").exists()



# D-558 T4(eval 164): 빈 방법 · 빈 기준 짝도 받음(Pack 43025a5 — 점검 what · 조치 what만 필수) · 관문이 끝내 실패하면 그 예외 편지만 보류
@pytest.mark.parametrize("field", ["how", "criteria"])
def test_empty_how_or_criteria_goes_to_extractor(R, field):
    it = {**ITEM[1], "check": {**ITEM[1]["check"], field: ""}}
    R.extract([(Path("x.json"), v9(items=[it]))], "g1")
    assert R.seen[0]["items"][0]["exception_record"]["items"] == [it]


def test_failed_gate_holds_only_the_exception_letter_history_letter_deploys(G):  # noqa: F811
    G.plan(*[{**FAIL, "candidate": narrow(x)} for x in (7.6, 7.5, 7.4, 7.3)])   # 매번 실패 + 다른 후보 → 고침 3번 뒤 보류
    letter(G, "RPT-000001", record=v9()["record"])
    letter(G, "RPT-000002", record=HISTORY)
    assert G.main() == "g2" and G.calls["swap"] == ["g2"]
    assert G.sig("RPT-000001")["status"] == "held" and G.sig("RPT-000001")["reason"] == "no_break" and G.sig("RPT-000001")["rounds"] == 4
    assert G.sig("RPT-000002")["status"] == "applied" and G.sig("RPT-000002")["rounds"] == 0 and G.sig("RPT-000002")["fixed"] == []
    assert G.bases[-1] is None                               # 다시 적재는 고친 그래프가 아니라 지금 도는 그래프 기준
    assert not (G.STATE / "nodes.json").exists()             # 이번 예외 관계는 쌓이지 않음
    assert (G.STATE / "done" / "RPT-000001").read_text().startswith("held") and not (G.STATE / "done" / "RPT-000002").read_text().startswith("held")


def test_failed_gate_history_letter_same_graph_signals_same(G):  # noqa: F811
    G.plan(*[{**FAIL, "candidate": narrow(x)} for x in (7.6, 7.5, 7.4, 7.3)])
    letter(G, "RPT-000001", record=v9()["record"])
    letter(G, "RPT-000002", record=HISTORY)
    G.load_tags = None
    real = G.load

    def load(tag=None, base_text=None):
        out = real(tag, base_text)
        return {**out, "tag": "L1"} if G.calls["load"] == 5 else out   # 예외 뺀 다시 적재 = 지금 도는 그래프와 같음
    G.load = load
    assert G.main() == "same" and G.calls["swap"] == []
    assert G.sig("RPT-000001")["status"] == "held" and G.sig("RPT-000002")["status"] == "same"



# D-561 ②: 11-04 기록(EX-R-0051 같은 고침 temp_diff_k<=7.5 세 번)을 지금 코드로 — 같은 고침은 두 번 쓰지 않음
def test_same_fix_is_not_applied_twice(G):  # noqa: F811
    G.plan(*[{**FAIL, "candidate": NARROW}] * 4)               # 관문이 매번 같은 후보를 냄(11-04 가짜 관문 plan-B와 같은 꼴)
    letter(G, record=v9()["record"])
    assert G.main() == "held"
    s = G.sig()
    assert s["fixed"] == [{"relation_id": "EX-R-0002", "change": "narrow", "where": "temp_diff_k<=7.6"}]   # 고침 한 번뿐
    assert s["rounds"] == 2 and G.calls["load"] == 2 and len(list((G.STATE / "gate").glob("input-*.json"))) == 2
    assert "같은 고침" in (G.STATE / "kg-apply.log").read_text()


def test_candidate_that_changes_nothing_is_not_tested_again(G):  # noqa: F811
    first = narrow(7.6)
    G.plan({**FAIL, "candidate": first}, {**FAIL, "candidate": narrow(7.5)}, {**FAIL, "candidate": first})   # 셋째가 앞 상태로 돌아가지 않아도 같은 상태면 멈춤
    letter(G, record=v9()["record"])
    assert G.main() == "held"
    assert [f["where"] for f in G.sig()["fixed"]] == ["temp_diff_k<=7.6", "temp_diff_k<=7.5"] and G.sig()["rounds"] == 3


# D-561 ①: 그래프 DB 옛 꼬리표 — 이 kg_apply가 만든 것만 · 지금 도는 것 + 앞 n벌 남김
def test_old_layers_are_dropped_keeping_running_and_previous(G, monkeypatch):  # noqa: F811
    monkeypatch.delenv("KG_GATE")
    dropped, deployed = [], []
    monkeypatch.setattr(G, "drop_layers", lambda tags: dropped.extend(tags) or {t: 1 for t in tags})
    monkeypatch.setattr(G, "keep_layers", lambda: set(deployed[-2:]))   # 지금 + 앞 1벌(기본 n = 1)
    real = G.load

    def load(tag=None, base_text=None):
        out = real(tag, base_text)
        deployed.append(out["tag"])
        return out
    monkeypatch.setattr(G, "load", load)
    for i in range(4):
        letter(G, f"RPT-00001{i}", record={"record_id": f"H-001{i}"})
        assert G.main() == "g2"
    assert deployed == ["L2", "L3", "L4", "L5"] and dropped == ["L2", "L3"]
    assert json.loads((G.STATE / "layers.json").read_text()) == ["L4", "L5"]


def test_held_run_drops_its_candidate_layers(G, monkeypatch):  # noqa: F811
    dropped = []
    monkeypatch.setattr(G, "drop_layers", lambda tags: dropped.extend(tags) or {t: 1 for t in tags})
    monkeypatch.setattr(G, "keep_layers", lambda: set())
    G.plan(*[{**FAIL, "candidate": narrow(x)} for x in (7.6, 7.5, 7.4, 7.3)])
    letter(G, record=v9()["record"])
    assert G.main() == "held"
    assert dropped == ["L2", "L3", "L4", "L5"]                 # 관문 시험에 쓴 후보 그래프 넷 — 처리가 끝난 뒤에만 지움


def test_keep_layers_reads_running_and_previous_tags(K, monkeypatch, tmp_path):  # noqa: F811
    import subprocess
    real = load("kg_apply_real_keep", "kg_apply.py")
    repo = tmp_path / "pack"
    (repo / "pack" / "templates" / "graph").mkdir(parents=True)
    g = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)   # noqa: E731
    g("init", "-q")
    for i, layer in enumerate(["LA", "LB", "LC", "LD"]):
        (repo / "pack" / "templates" / "graph" / "current.json").write_text(json.dumps({"layer": layer}))
        g("add", "-A"); g("commit", "-q", "-m", layer); g("tag", f"g{i}")
    monkeypatch.setattr(real.S, "pack_repo", lambda: repo)
    monkeypatch.setattr(real, "running", lambda: ("g3", "r", "LD"))
    assert real.keep_layers() == {"LD", "LC"}
    monkeypatch.setenv("KG_KEEP_LAYERS", "2")
    assert real.keep_layers() == {"LD", "LC", "LB"}
    monkeypatch.setattr(real, "running", lambda: ("g1", "r", "LB"))   # 되돌린 뒤 — 지금 도는 것 기준
    assert real.keep_layers() == {"LB", "LA"} | set()


def test_extractor_gets_original_item_count(R):
    """llmops-2 173: '아님' 하나가 0번이면 EX-<기록>인지 EX-<기록>-1인지 원래 item 수로 가림 — item_count 한 칸."""
    R.extract([(Path("x.json"), v9([{"item": 0, "decision": "not", "match": None}, {"item": 1, "decision": "same", "match": None},
                                    {"item": 2, "decision": "same", "match": None}]))], "g1")
    er = R.seen[0]["items"][0]["exception_record"]
    assert er["item_index"] == [0] and er["item_count"] == 3
