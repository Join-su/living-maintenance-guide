"""kg_apply 후보 그래프 폴더 입구(D-560 2단계 · candidate-graph-inlet.md · dev-2) 가짜 시험 — 관문 · 적재기 · 서버 모두 가짜, 네트워크 · DB 0.
보는 것: 까닭 넷(add · widen · replace · merge) 각 한 파일 → 같은 관문(letters [] · candidate 칸) → 배포 · 신호 candidate 칸
· 실패 쪽(base_moved · rows_rejected · 꼴 틀림 · 뜻 안 맞음 bad_candidate · 관문 실패 · 적재기 실패) · 반영 뒤 nodes.json 줄 = release 줄(고침 표시 되풀이 0)."""
import json

import pytest

from test_kg_apply import K, REV1, letter  # noqa: F401 - fixture K
from test_kg_gate import FAIL, G, NARROW, OK  # noqa: F401 - fixture G

SEC_A = {"no": "EX-R-0001", "kind": "예외", "grade": "C", "name": "예외상황-필터 덮개 나사", "group": "온도 차 저하", "minutes": None,
         "checklist": [], "records": ["R-0001"], "topic": "필터 덮개 나사",
         "checks": [{"what": "덮개 고정 나사", "how": "네 개가 다 있는지 본다", "criteria": "", "minutes": 5}],
         "actions": [{"what": "빠진 나사 새로 끼우고 모두 조임", "parts": [], "minutes": 5}]}
REL_A = {"id": "EX-R-0001", "step": "EX-R-0001", "mode": "안내", "grade": "C", "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.2]]},
         "where": "temp_diff_k<=8.2", "cause": "CL-1", "records": ["R-0001"], "n": 1}
SEC_B = {**SEC_A, "no": "EX-R-0005", "name": "예외상황-덮개 패킹", "topic": "덮개 패킹", "records": ["R-0005"]}
REL_B = {**REL_A, "id": "EX-R-0005", "step": "EX-R-0005", "records": ["R-0005"], "where": "temp_diff_k<=7.0 · M-03",
         "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 7.0]], "equipment_id": "M-03"}}
ROWS = {   # extractor-rows.md 1 ~ 4절 예 그대로(줄여 씀)
    "add": {"sections": [], "exception_relations": [{**REL_A, "id": "EX-R-0009", "records": ["R-0009"],
                                                     "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.2], ["torque_nm", ">=", 55]]},
                                                     "where": "temp_diff_k<=8.2 · torque_nm>=55"}]},
    "widen": {"sections": [], "exception_relations": [{**REL_A, "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 9.0]]},
                                                       "where": "temp_diff_k<=9.0", "records": ["R-0001", "R-0009"], "n": 2}]},
    "replace": {"sections": [{**SEC_A, "edit_record": "R-0012"}], "exception_relations": []},
    "merge": {"sections": [{**SEC_A, "records": ["R-0001", "R-0005"], "edit_record": "R-0013"}],
              "exception_relations": [{**REL_B, "step": "EX-R-0001"}]},
}
CID = {"add": "KGC-c1a15-0001-1a2b3c4d", "widen": "KGC-c1a15-0002-2b3c4d5e", "replace": "KGC-c1a15-0003-3c4d5e6f", "merge": "KGC-c1a15-0004-4d5e6f70"}


def cand(reason="add", cid=None, rows=None, **kw):
    cid = cid or CID[reason]
    return {"format": "kg-candidate@1", "candidate_id": cid, "base": {"tag": "g1", "layer": "L1", "revision": REV1}, "reason": reason,
            "source": {"kind": "overlap" if reason in ("replace", "merge") else "missed", "records": ["R-0009"], "relations": ["EX-R-0001"], "wheel": 7},
            "made_by": "llmops-auto", "decided_by": "u-lead" if reason in ("replace", "merge") else None, "rows": rows or ROWS[reason], **kw}


@pytest.fixture
def C(G, monkeypatch, tmp_path):   # noqa: F811
    """후보 폴더 · 지금 도는 그래프(관계 A가 절 A를 가리킴) · 적재기 가짜가 current.json을 씀(nodes.json 줄에서 고침 표시를 뗀 release 꼴)."""
    d = tmp_path / "kg-candidates"
    d.mkdir()
    monkeypatch.setenv("KG_CANDIDATES", str(d))
    G.CANDS = d
    monkeypatch.setattr(G, "git", lambda *a: json.dumps({"layer": "L1", "sections": [SEC_A, SEC_B], "exception_relations": [REL_A, REL_B]}))
    G.loader_rows = []

    def fake_load(tag=None, base_text=None):
        G.calls["load"] += 1
        G.bases.append(base_text)
        p = G.STATE / "nodes.json"
        acc = json.loads(p.read_text()) if p.exists() else {"sections": [], "exception_relations": []}
        rel = {"layer": f"L{1 + G.calls['load']}",
               "sections": [{**{k: v for k, v in s.items() if k != "edit_record"}, **({"previous": [{"record": s["edit_record"]}]} if "edit_record" in s else {})}
                            for s in acc["sections"]],
               "exception_relations": [{k: v for k, v in x.items() if k != "edit"} for x in acc["exception_relations"]]}
        (G.STATE / "current.json").write_text(json.dumps(rel, ensure_ascii=False))
        return {"tag": rel["layer"], "release_id": "graph-x", "rejected": [], "rejected_rows": list(G.loader_rows)}

    monkeypatch.setattr(G, "load", fake_load)
    G.put = lambda c, name=None: (d / f"{name or c['candidate_id']}.json").write_text(json.dumps(c, ensure_ascii=False))
    return G


@pytest.mark.parametrize("reason", ["add", "widen", "replace", "merge"])
def test_each_reason_goes_through_the_same_gate_and_deploys(C, reason):
    C.plan(OK)
    C.put(cand(reason))
    assert C.main() == "g2"
    assert C.calls["extract"] == 0 and C.calls["load"] == 1 and C.calls["swap"] == ["g2"]
    inp = C.seen(1)
    assert inp["format"] == "kg-gate/input@1" and inp["letters"] == [] and inp["running_tag"] == "g1"
    assert inp["candidate"] == {"candidate_id": CID[reason], "reason": reason, "source": cand(reason)["source"], "made_by": "llmops-auto",
                                "decided_by": "u-lead" if reason in ("replace", "merge") else None, "rows": ROWS[reason]}   # rows = 후보 파일 그대로(kggate 옮겨 적기 셈)
    assert inp["new_relations"] == {"add": ["EX-R-0009"], "widen": ["EX-R-0001"], "replace": ["EX-R-0001"], "merge": ["EX-R-0005", "EX-R-0001"]}[reason]
    s = C.sig(CID[reason])
    assert s["format"] == "kg-apply/status@1" and s["report_id"] == CID[reason] and s["record_id"] is None
    assert s["status"] == "applied" and s["graph"] == {"running_tag": "g1", "new_tag": "g2"} and s["rounds"] == 1
    assert s["candidate"] == {k: inp["candidate"][k] for k in ("reason", "source", "made_by", "decided_by")}
    assert s["checks"] == OK["checks"]
    assert [e["status"] for e in C.events()] == ["checking", "applied"]
    assert (C.STATE / "done-candidates" / CID[reason]).exists() and not (C.STATE / "done" / CID[reason]).exists()
    assert C.main() == "none" and C.calls["load"] == 1           # 처리한 후보는 다시 0


def test_after_apply_nodes_rows_are_release_rows_without_edit_marks(C):
    C.plan(OK)
    C.put(cand("replace"))
    C.main()
    secs = json.loads((C.STATE / "nodes.json").read_text())["sections"]
    assert len(secs) == 1 and "edit_record" not in secs[0] and secs[0]["previous"] == [{"record": "R-0012"}]
    C.plan(OK)
    letter(C, record={"record_id": "R-0020"})                      # 다음 승인 기록 적재: 고침이 다시 쌓이지 않음
    C.main()
    assert json.loads((C.STATE / "current.json").read_text())["sections"][0]["previous"] == [{"record": "R-0012"}]


def test_widen_replaces_the_same_id_in_nodes(C):
    (C.STATE).mkdir(exist_ok=True)
    (C.STATE / "nodes.json").write_text(json.dumps({"sections": [], "exception_relations": [REL_A]}))   # 앞 승인 기록이 쌓은 옛 줄
    C.plan(OK)
    C.put(cand("widen"))
    C.main()
    rels = json.loads((C.STATE / "nodes.json").read_text())["exception_relations"]
    assert [r["where"] for r in rels] == ["temp_diff_k<=9.0"] and rels[0]["n"] == 2


def test_base_moved_holds_without_load_or_gate(C):
    C.plan("crash")
    C.put(cand("add", base={"tag": "g0", "layer": "L0", "revision": "sha256:" + "9" * 64}))
    assert C.main() == "held"
    assert C.calls["load"] == 0 and C.calls["swap"] == [] and not (C.STATE / "gate").exists()
    s = C.sig(CID["add"])
    assert s["status"] == "held" and s["reason"] == "base_moved" and s["checks"]["running_revision"] == REV1
    assert (C.STATE / "done-candidates" / CID["add"]).read_text().startswith("held")


def test_short_base_revision_prefix_is_same(C):
    C.plan(OK)
    C.put(cand("add", base={"tag": "g1", "layer": "L1", "revision": "11111111"}))
    assert C.main() == "g2"


def test_rows_rejected_holds_and_restores_nodes(C):
    C.plan("crash")
    C.loader_rows = [{"row": "EX-R-0001", "why": "고칠 예외가 그래프에 없음"}]
    C.put(cand("replace"))
    assert C.main() == "held"
    assert C.calls["swap"] == [] and not (C.STATE / "gate").exists() and not (C.STATE / "nodes.json").exists()
    s = C.sig(CID["replace"])
    assert s["reason"] == "rows_rejected" and s["checks"]["rejected_rows"] == C.loader_rows


def test_other_rows_rejected_does_not_hold(C):
    C.plan(OK)
    C.loader_rows = [{"row": "EX-R-0777", "why": "센서 칸 이름"}]   # 후보 줄 아님(앞에서 쌓인 줄) — 후보는 그대로
    C.put(cand("add"))
    assert C.main() == "g2"


@pytest.mark.parametrize("change,where", [
    ({"format": "kg-candidate@0"}, "format"),
    ({"candidate_id": "KGC-c1a15-0009-ffffffff"}, "candidate_id"),
    ({"base": {"tag": "g1"}}, "base.revision"),
    ({"reason": "delete"}, "reason"),
    ({"source": {"kind": "guess"}}, "source.kind"),
    ({"made_by": ""}, "made_by"),
    ({"decided_by": "x", "rows": None}, "rows"),
    ({"decided_by": 3}, "decided_by"),
    ({"rows": {"sections": [], "exception_relations": []}}, "rows"),
    ({"rows": {"nodes": []}}, "rows"),
    ({"rows": {"exception_relations": [REL_A, REL_A]}}, "rows.exception_relations"),
    ({"rows": {"sections": [{"kind": "예외"}]}}, "rows.sections"),
])
def test_bad_shape_fails_once_without_mark(C, change, where):
    C.plan("crash")
    C.put({**cand("add"), **change}, name=CID["add"])
    assert C.main() == "failed"
    assert C.calls["load"] == 0 and C.calls["swap"] == [] and not (C.STATE / "done-candidates" / CID["add"]).exists()
    assert C.sig(CID["add"])["status"] == "failed" and C.sig(CID["add"])["reason"] == f"후보 꼴 틀림: {where}"
    C.main()
    assert len(C.events()) == 1                                    # 같은 파일 알림은 한 번


def test_broken_json_fails(C):
    (C.CANDS / f"{CID['add']}.json").write_text("{")
    assert C.main() == "failed" and C.sig(CID["add"])["reason"] == "후보 꼴 틀림: format"


def test_gate_fail_without_fix_holds_and_restores_nodes(C):
    C.plan(FAIL)
    C.put(cand("add"))
    assert C.main() == "held"
    assert C.calls["swap"] == [] and not (C.STATE / "nodes.json").exists()
    assert C.sig(CID["add"])["reason"] == "no_break" and C.sig(CID["add"])["rounds"] == 1


def test_gate_fix_on_candidate_relation_then_deploys(C):
    C.plan({**FAIL, "candidate": {**NARROW, "relation_id": "EX-R-0009"}}, OK)
    C.put(cand("add"))
    assert C.main() == "g2"
    rel = json.loads((C.STATE / "nodes.json").read_text())["exception_relations"][0]
    assert rel["id"] == "EX-R-0009" and rel["where"] == NARROW["where"]   # 고친 결과 = release 줄
    assert C.sig(CID["add"])["fixed"] == [{"relation_id": "EX-R-0009", "change": "narrow", "where": NARROW["where"]}]


def test_loader_failure_restores_nodes_and_signals_failed(C, monkeypatch):
    def boom(tag=None, base_text=None):
        raise C.ApplyError("적재기 실패")
    monkeypatch.setattr(C, "load", boom)
    C.put(cand("add"))
    with pytest.raises(SystemExit):
        C.main()
    assert not (C.STATE / "nodes.json").exists() and not (C.STATE / "done-candidates" / CID["add"]).exists()
    assert C.sig(CID["add"])["status"] == "failed"


def test_letters_first_then_candidate_one_at_a_time(C):
    C.plan(OK)
    letter(C, record={"record_id": "R-0021"})                      # 일반 이력 — 관문 0
    C.put(cand("add"))
    C.put(cand("widen"))
    assert C.main() == "g2"
    assert C.calls["swap"] == ["g2", "g2", "g2"] and C.calls["load"] == 3   # 편지 한 번 · 후보 하나씩
    assert C.sig("RPT-000001")["status"] == "applied" and "candidate" not in C.sig("RPT-000001")
    assert C.seen(1)["candidate"]["candidate_id"] == CID["widen"]  # 마지막 관문 입력 = 두 번째 후보


def test_without_candidates_folder_nothing_changes(G, monkeypatch):   # noqa: F811
    monkeypatch.delenv("KG_CANDIDATES", raising=False)
    assert G.main() == "none"


WIDE = ROWS["widen"]["exception_relations"][0]


@pytest.mark.parametrize("reason,rows,extra,codes", [   # eval T8 뜻 안 맞음 일곱 + when 그대로
    ("widen", ROWS["add"], {}, ["widen_needs_same_id"]),
    ("add", ROWS["widen"], {}, ["add_needs_new_id"]),
    ("replace", {"sections": [SEC_A], "exception_relations": []}, {}, ["replace_needs_edit_record"]),
    ("merge", {"sections": ROWS["merge"]["sections"], "exception_relations": [{**REL_B, "edit": True}]}, {}, ["merge_needs_step_move"]),
    ("widen", {"exception_relations": [{**WIDE, "edit": True, "records": ["R-0009"], "n": 1}]}, {}, ["widen_not_edit_path", "widen_records_n"]),
    ("widen", {"exception_relations": [{k: v for k, v in WIDE.items() if k not in ("records", "n")}]}, {}, ["widen_records_n"]),
    ("merge", ROWS["merge"], {"decided_by": None}, ["merge_needs_person"]),
    ("widen", {"exception_relations": [{**WIDE, "when": REL_A["when"]}]}, {}, ["widen_when_same"]),
])
def test_meaning_mismatch_holds_bad_candidate_without_gate(C, reason, rows, extra, codes):
    C.plan("crash")
    C.put(cand(reason, rows=rows, **extra))
    assert C.main() == "held"
    assert C.calls["swap"] == [] and not (C.STATE / "gate").exists() and not (C.STATE / "nodes.json").exists()
    s = C.sig(CID[reason])
    assert s["status"] == "held" and s["reason"] == "bad_candidate" and s["checks"] == {"inlet": codes} and s["rounds"] == 0
    assert (C.STATE / "done-candidates" / CID[reason]).read_text().startswith("held")


def test_missing_decided_by_key_is_bad_shape(C):
    c = cand("add")
    del c["decided_by"]
    C.put(c)
    assert C.main() == "failed" and C.sig(CID["add"])["reason"] == "후보 꼴 틀림: decided_by"


def test_letters_off_leaves_approval_files_and_still_takes_candidates(C, monkeypatch):
    monkeypatch.setenv("KG_LETTERS", "off")
    C.plan(OK)
    letter(C, record={"record_id": "R-0021"})
    C.put(cand("add"))
    assert C.main() == "g2"
    assert C.calls["swap"] == ["g2"] and not (C.STATE / "done" / "RPT-000001").exists()   # 승인 기록 파일은 반영 0(LLMOps 길)
    assert C.sig(CID["add"])["status"] == "applied"


def test_letters_bad_value_refused(C, monkeypatch):
    monkeypatch.setenv("KG_LETTERS", "maybe")
    with pytest.raises(SystemExit):
        C.main()
