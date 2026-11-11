# ruff: noqa: E501 — 시험 설명 문장은 길이 제한 밖
"""D-633 예외 추천 '안 고름' 기록(순수 함수): start 결과 exception_guide가 실린 세션 = 예외가 보임(진단 카드 · [점검 바꾸기] 후보 줄).
사람이 그 예외 절을 끝까지 고르지 않으면 adopted false — 길 how = switched([점검 바꾸기]로 다른 점검 · [예외] EX-NEW) |
followed(추천 점검대로 감 · 창을 열고 취소한 것도 사건이 없어 여기). 칸 = 관계 id · 그 안내가 처음 실린 Run id ·
사람이 한 점검(picked_section). 체크 기록 v1 선택 칸 exception_offer · 내부 기록 줄 kind exception_not_picked."""

EG = {"section": "EX-R-0001", "relation_id": "EX-R-0001", "grade": "C", "title": "필터 덮개 고정 나사 확인"}
START = "run_01M6SJP7QH5VX990R4EMVRW7HY"


def _base():
    return [
        {"seq": 1, "type": "step_opened", "run_id": START, "decision": {"kind": "lock"}, "exception_guide": EG},
        {"seq": 2, "type": "preview_shown", "preview": {"candidates": [{"section": "EX-R-0001", "exception": True}]}},
        {"seq": 3, "type": "alarm_ack"},
        {"seq": 4, "type": "lock"},
        {"seq": 5, "type": "precheck_done", "values": "52 °C"},
        {"seq": 6, "type": "step_opened", "run_id": "run_B", "decision": {"kind": "instruct", "section": "4.3"}},
    ]


def _confirm(seq):
    return {"seq": seq, "type": "report_confirm", "exception_record": {}}


def test_followed_recommended_check_is_not_picked():
    from c1a15.api import flow

    ev = [*_base(), {"seq": 7, "type": "result", "section": "4.3", "value": "이상"}]
    assert flow.exception_offer(ev) == {
        "relation_id": "EX-R-0001",
        "run_id": START,
        "adopted": False,
        "how": "followed",
        "picked_section": "4.3",
    }


def test_followed_before_any_result_takes_first_instruction():
    from c1a15.api import flow

    assert flow.exception_offer(_base())["picked_section"] == "4.3"


def test_switched_to_other_check_is_not_picked():
    from c1a15.api import flow

    ev = [
        *_base(),
        {"seq": 7, "type": "switch_check", "section": "4.5", "from_section": "4.3", "choice": "candidate"},
        {"seq": 8, "type": "step_opened", "run_id": "run_C", "decision": {"kind": "instruct", "section": "4.5"}},
        {"seq": 9, "type": "result", "section": "4.5", "value": "정상"},
    ]
    got = flow.exception_offer(ev)
    assert (got["adopted"], got["how"], got["picked_section"], got["run_id"]) == (False, "switched", "4.5", START)


def test_own_exception_start_is_switched_to_ex_new():
    from c1a15.api import flow

    ev = [*_base(), {"seq": 7, "type": "exception_start", "mode": "act_now"}]
    got = flow.exception_offer(ev)
    assert (got["adopted"], got["how"], got["picked_section"]) == (False, "switched", "EX-NEW")


def test_picking_the_exception_is_adopted_even_if_switched_back():
    from c1a15.api import flow

    pick = {"seq": 7, "type": "switch_check", "section": "EX-R-0001", "from_section": "4.3", "choice": "candidate"}
    back = {"seq": 8, "type": "switch_check", "section": "4.3", "from_section": "EX-R-0001", "choice": "candidate"}
    for ev in ([*_base(), pick], [*_base(), pick, back]):
        got = flow.exception_offer(ev)
        assert (got["adopted"], got["how"], got["picked_section"]) == (True, None, "EX-R-0001")
    diag = {"seq": 4.5, "type": "diagnosis_pick", "section": "EX-R-0001", "from_section": "4.3", "choice": "candidate"}
    assert flow.exception_offer([*_base(), diag])["adopted"] is True  # 잠금 전 진단 카드에서 고름


def test_void_pick_does_not_count():
    from c1a15.api import flow

    pick = {"seq": 7, "type": "switch_check", "section": "EX-R-0001", "from_section": "4.3", "void": True}
    assert flow.exception_offer([*_base(), pick])["adopted"] is False


def test_no_exception_guide_is_none():
    from c1a15.api import flow

    ev = [{**e, "exception_guide": None} if e["type"] == "step_opened" else e for e in _base()]
    assert flow.exception_offer(ev) is None


def test_check_records_line_after_report_confirm_only():
    from c1a15.api.sessions import check_records

    ev = [*_base(), {"seq": 7, "type": "result", "section": "4.3", "value": "이상"}]
    assert check_records(9, ev) == []  # 확정 전 = 기록 줄 0(끝까지 안 고름은 확정 때 정해짐)
    assert check_records(9, [*ev, _confirm(8)]) == [
        {
            "kind": "exception_not_picked",
            "session_id": 9,
            "human_check": {"section": "4.3", "from_section": None},
            "relation_id": "EX-R-0001",
            "at_seq": 8,
            "how": "followed",
            "run_id": START,
        }
    ]
    pick = {"seq": 7, "type": "switch_check", "section": "EX-R-0001", "from_section": "4.3", "choice": "candidate"}
    assert check_records(9, [*_base(), pick, _confirm(8)]) == []  # 고름 = 안 고름 줄 0


def test_model_accepts_offer_and_defaults_to_null():
    from c1a15.checkrecord.model import CheckRecordV1, ExceptionOffer

    assert CheckRecordV1.model_fields["exception_offer"].default is None
    o = {"relation_id": "EX-R-0001", "run_id": START, "adopted": False, "how": "followed", "picked_section": "4.3"}
    assert ExceptionOffer.model_validate(o).model_dump() == o
