"""kg_apply가 부르는 꼴 — Pack 약속 v10(D-566 · 덧 1 · dev-2) 가짜 시험, 네트워크 · DB · 실제 호출 0.
예외 하나 = topic + checks(점검 목록) + actions(조치 목록) · 같음/아님은 예외 전체에 한 번(exception_review {decision, match}).
보는 것: '아님'이면 예외 통째로 추출기 · 관문에(적은 순서 그대로) · '같음'이면 그래프 0 · 점검 0 또는 조치 0 = 나누지 못함(관문 0 · held)
· 칸 꼴이 틀리면 추출기 부르기 전에 멈춤 · v9 짝 꼴 편지는 그대로 받음(test_kg_v9)."""
import json
from pathlib import Path

import pytest

from test_kg_apply import K, LETTER, letter, load  # noqa: F401 - fixture K
from test_kg_gate import FAIL, G, NARROW, OK  # noqa: F401 - fixture G
from test_kg_v9 import R  # noqa: F401 - fixture R(진짜 extract · 가짜 추출기)

EXC = {"topic": "필터 덮개 나사",
       "checks": [{"what": "필터 덮개 고정 나사", "how": "덮개 나사 네 개를 손으로 돌려 본다", "criteria": "네 개 모두 체결 = 정상", "minutes": None},
                  {"what": "흡기 덕트 이음매", "how": "", "criteria": "", "minutes": 3}],
       "actions": [{"what": "풀린 나사 다시 조임", "parts": [], "minutes": None},
                   {"what": "덕트 밴드 다시 조임", "parts": ["밴드"], "minutes": 5}]}
HISTORY = {"record_id": "H-0003"}


def v10(review={"decision": "not", "match": None}, exception=EXC, rid="R-0301"):
    er = {"situation": "s", "exception": exception, "result": "재가동 정상", "note": None}
    if review is not None:
        er["exception_review"] = review
    return {"record_id": rid, "session_id": "S-9", "human_check": {"section": "EX-NEW", "from_section": "4.3"}, "exception_record": er}


def test_not_goes_whole_to_extractor_in_written_order(R):  # noqa: F811
    assert R.extract([(Path("x.json"), {**LETTER, "record": v10()})], "g1") == 1
    er = R.seen[0]["items"][0]["exception_record"]
    assert er["kind"] == "new_exception" and er["exception"] == EXC
    assert [c["what"] for c in er["exception"]["checks"]] == ["필터 덮개 고정 나사", "흡기 덕트 이음매"]
    assert [a["what"] for a in er["exception"]["actions"]] == ["풀린 나사 다시 조임", "덕트 밴드 다시 조임"]
    assert "exception_review" not in er and "items" not in er and "item_index" not in er and "item_count" not in er


def test_without_review_the_exception_is_new(R):  # noqa: F811
    R.extract([(Path("x.json"), {**LETTER, "record": v10(review=None)})], "g1")
    assert R.seen[0]["items"][0]["exception_record"]["exception"] == EXC


def test_same_means_no_extraction(R):  # noqa: F811
    assert R.extract([(Path("x.json"), {**LETTER, "record": v10({"decision": "same", "match": {"kind": "예외", "no": "EX-R-0001"}})})], "g1") == 0
    assert R.seen == []


@pytest.mark.parametrize("bad,where", [
    ({"checks": [{"what": ""}]}, "checks[0].what"),
    ({"checks": [{"what": "x", "how": 3}]}, "checks[0].how"),
    ({"checks": [{"what": "x", "minutes": 0}]}, "checks[0].minutes"),
    ({"actions": [{"what": "y", "parts": "밴드"}]}, "actions[0].parts"),
    ({"actions": [{"what": ""}]}, "actions[0].what"),
    ({"topic": 7}, "topic"),
    ({"checks": [{"what": "x"}] * 11}, "checks"),
])
def test_bad_exception_stops_before_extractor(R, bad, where):  # noqa: F811
    with pytest.raises(R.ApplyError, match=f"예외 칸 꼴 틀림: .* {where.replace('[', '[[]')}"):
        R.extract([(Path("x.json"), {**LETTER, "record": v10(exception={**EXC, **bad})})], "g1")
    assert R.seen == []


@pytest.mark.parametrize("review", [{"decision": "maybe"}, [{"item": 0, "decision": "not"}], "not"])
def test_bad_review_stops(R, review):  # noqa: F811
    with pytest.raises(R.ApplyError, match="exception_review"):
        R.extract([(Path("x.json"), {**LETTER, "record": v10(review)})], "g1")


@pytest.mark.parametrize("exc", [None, {**EXC, "checks": []}, {**EXC, "actions": []}, {"topic": "t", "checks": [], "actions": []}])
def test_one_side_empty_is_not_split_and_held_without_gate(G, exc):  # noqa: F811
    G.plan("crash")                                              # 관문이 불리면 보류가 됨 — 안 불려야 함
    letter(G, record=v10(exception=exc))
    assert G.main() == "held"
    assert G.calls["extract"] == 0 and G.calls["load"] == 0 and not (G.STATE / "gate").exists()
    s = G.sig()
    assert s["status"] == "held" and s["reason"] == "not_split" and s["rounds"] == 0


def test_gate_input_carries_the_whole_exception(G):  # noqa: F811
    G.plan(OK)
    letter(G, record=v10())
    assert G.main() == "g2"
    x = G.seen(1)["letters"][0]
    assert x["exception_record"]["kind"] == "new_exception" and x["exception_record"]["exception"] == EXC
    assert x["session_id"] == "S-9" and x["human_check"] == {"section": "EX-NEW", "from_section": "4.3"}


def test_same_letter_skips_gate(G):  # noqa: F811
    G.plan("crash")
    letter(G, record=v10({"decision": "same", "match": None}))
    assert G.main() == "g2" and not (G.STATE / "gate").exists()


def test_not_split_held_and_history_letter_applied(G):  # noqa: F811
    G.plan("crash")
    letter(G, "RPT-000001", record=v10(exception={**EXC, "actions": []}))
    letter(G, "RPT-000002", record=HISTORY)
    assert G.main() == "g2"
    assert G.sig("RPT-000001")["reason"] == "not_split" and G.sig("RPT-000002")["status"] == "applied"


def test_v9_and_v10_letters_in_one_run(R):  # noqa: F811
    from test_kg_v9 import ITEM
    v9 = {"record_id": "R-0002", "exception_record": {"situation": "s", "items": ITEM[:1], "exception_review": [{"item": 0, "decision": "not", "match": None}]}}
    R.extract([(Path("a.json"), {**LETTER, "report_id": "RPT-A", "record": v9}), (Path("b.json"), {**LETTER, "report_id": "RPT-B", "record": v10()})], "g1")
    ers = [x["exception_record"] for x in R.seen[0]["items"]]
    assert ers[0]["items"] == ITEM[:1] and ers[1]["exception"] == EXC


# D-560 7(same-edit.md): 같음 + 고침/보탬 — exception_review.edit {checks, actions} = 대상 예외의 고친 뒤 목록 전체
EDIT = {"checks": [{"what": "필터 덮개 고정 나사", "how": "나사 네 개를 손으로 돌려 보고 풀린 나사가 있으면 손으로 돌려 본다", "criteria": "네 개 모두 체결 = 정상", "minutes": None}],
        "actions": [{"what": "빠진 나사는 새로 끼우고 남은 나사도 다시 조인다", "parts": ["나사"], "minutes": None}]}
SAME_EDIT = {"decision": "same", "match": {"kind": "예외", "no": "EX-R-0001"}, "edit": EDIT}


def test_same_edit_goes_to_extractor_and_gate(R):  # noqa: F811
    assert R.extract([(Path("x.json"), {**LETTER, "record": v10(SAME_EDIT)})], "g1") == 1
    er = R.seen[0]["items"][0]["exception_record"]
    assert er["kind"] == "same_edit" and er["exception_review"] == SAME_EDIT and er["exception"] == EXC


def test_same_without_edit_is_still_missed_only(R):  # noqa: F811
    for review in ({"decision": "same", "match": {"kind": "예외", "no": "EX-R-0001"}, "edit": None},
                   {"decision": "same", "match": {"kind": "예외", "no": "EX-R-0001"}}):
        assert R.extract([(Path("x.json"), {**LETTER, "record": v10(review)})], "g1") == 0
    assert R.seen == []


@pytest.mark.parametrize("review,where", [
    ({**SAME_EDIT, "match": {"kind": "매뉴얼", "no": "4.5"}}, "edit"),            # 매뉴얼 절은 고치지 않음
    ({**SAME_EDIT, "match": None}, "edit"),
    ({**SAME_EDIT, "decision": "not"}, "edit"),                                  # edit는 같음일 때만
    ({**SAME_EDIT, "edit": {"checks": [], "actions": EDIT["actions"]}}, "edit.checks"),
    ({**SAME_EDIT, "edit": {"checks": [{"what": ""}], "actions": EDIT["actions"]}}, "edit.checks[0].what"),
    ({**SAME_EDIT, "edit": "x"}, "edit"),
])
def test_bad_same_edit_stops_before_extractor(R, review, where):  # noqa: F811
    with pytest.raises(R.ApplyError, match=f"예외 칸 꼴 틀림: .* exception_review.{where.replace('[', '[[]')}"):
        R.extract([(Path("x.json"), {**LETTER, "record": v10(review)})], "g1")
    assert R.seen == []


def test_same_edit_letter_goes_through_gate(G):  # noqa: F811
    G.plan(OK)
    letter(G, record=v10(SAME_EDIT))
    assert G.main() == "g2"
    x = G.seen(1)["letters"][0]["exception_record"]
    assert x["kind"] == "same_edit" and x["exception_review"]["edit"] == EDIT


def test_same_edit_is_not_not_split_even_if_ai_split_was_empty(G):  # noqa: F811
    G.plan(OK)
    letter(G, record=v10(SAME_EDIT, exception={**EXC, "actions": []}))   # 근거 나눔이 한쪽 0이어도 같음 + 고침은 대상 예외를 바꿈
    assert G.main() == "g2" and G.sig()["status"] == "applied"
