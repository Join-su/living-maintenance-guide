"""P-19 화면 값 unit 시험(표 기반, DB 없음): 교대 · 점검 n / 최대 m · 근거 기록 · 원인 이름 · 센서 표 · 절차."""

import json
from datetime import datetime

import pytest

from c1a15.api import screen
from c1a15.engine import alarm_rules, knowledge
from c1a15.llm.base import Draft
from c1a15.loader import core

KST = screen.KST


@pytest.fixture(scope="module")
def k(data_dir_module):
    return knowledge.from_sections(core.read_manual(data_dir_module))


@pytest.fixture(scope="module")
def grades(data_dir_module):
    rows = core.grade_rows(core.read_manual(data_dir_module))
    return {r["section_id"]: r["grade"] for r in rows}, {r["section_id"]: r["minutes"] for r in rows}


@pytest.fixture(scope="module")
def table(data_dir_module):
    return alarm_rules.load(data_dir_module / "alarm_rules.json")


@pytest.mark.parametrize(
    ("now", "start"),
    [
        ("2026-10-13T23:30", "2026-10-13T22:00"),  # 야간 앞부분
        ("2026-10-14T03:00", "2026-10-13T22:00"),  # 자정 넘어
        ("2026-10-14T05:59", "2026-10-13T22:00"),
        ("2026-10-14T06:00", "2026-10-13T22:00"),  # 주간 시작 = 방금 끝난 야간이 가장 가까움
        ("2026-10-14T13:59", "2026-10-13T22:00"),
        ("2026-10-14T14:00", "2026-10-13T22:00"),  # 같은 거리(8시간)면 지난 야간
        ("2026-10-14T14:01", "2026-10-14T22:00"),  # 다가올 야간이 더 가까움
        ("2026-10-14T22:00", "2026-10-14T22:00"),
    ],
)
def test_shift_window_is_fixed_night_or_nearest(now, start):
    """결정 014 ④: 야간 22:00 ~ 06:00 고정, 주간이면 가장 가까운 야간 교대."""
    s, e = screen.shift_window(datetime.fromisoformat(now).replace(tzinfo=KST))
    assert s == datetime.fromisoformat(start).replace(tzinfo=KST) and (e - s).total_seconds() == 8 * 3600


def test_shift_summary_counts_before_s03():
    out = screen.shift_summary(2, datetime(2026, 10, 14, 1, 0, tzinfo=KST))
    assert out["label"] == "야간" and (out["start_text"], out["end_text"]) == ("22:00", "06:00")
    assert (out["done"], out["in_progress"], out["calls"]) == (0, 2, 0)  # 보고서 · 호출은 S-03부터


def opened(section, kind="instruct"):
    return {"type": "step_opened", "decision": {"kind": kind, "section": section}}


def test_check_progress_definition(k, grades):
    """D-637 F3: n = 지시된 점검 순번, m = 이 세션에서 갈 수 있는 점검 모두 — 매뉴얼 점검 중 D가 아닌 것
    (A · B, C는 [점검 바꾸기] → 파트장 승인 뒤) ∪ 예외 절(안내가 있으면) ∪ 이미 지시된 점검. 바꾸기로 가도 n ≤ m."""
    g = dict(grades[0])
    assert (g["4.3"], g["4.4"], g["4.5"], g["4.6"]) == ("A", "C", "A", "A")
    base = [opened("1.2", "lock"), opened("4.2", "precheck")]
    assert screen.check_progress(base, k, "CL", g, set()) == {"check_no": 0, "check_max": 4}  # C 4.4도 셈
    ev = base + [opened("4.3"), opened("4.3"), opened("4.5")]  # 같은 점검이 다시 열려도 한 번
    assert screen.check_progress(ev, k, "CL", g, set()) == {"check_no": 2, "check_max": 4}
    assert screen.check_progress(ev, k, "CL", g, {"4.4"}) == {"check_no": 2, "check_max": 4}  # 승인돼도 같은 수
    g2 = {**g, "4.5": "D"}  # 지시 뒤 D가 된 점검도 '한 점검'으로 남는다
    assert screen.check_progress(ev, k, "CL", g2, set()) == {"check_no": 2, "check_max": 4}
    g3 = {**g, "4.4": "D"}  # D는 갈 수 없어 빠짐
    assert screen.check_progress(base, k, "CL", g3, set())["check_max"] == 3
    assert screen.check_progress(base + [opened("4.4", "call")], k, "CL", g, set())["check_no"] == 0  # 호출은 지시 아님


def test_check_progress_rehearsal_switch_never_exceeds_max(k, grades):
    """D-637 F3(11-08 리허설): 4.3 정상 → 4.6 → [점검 바꾸기] 4.4(C · 승인) → 4.5 → 예외 — 끝까지 n ≤ m, m은 그대로."""
    g = dict(grades[0])
    eg = {"section": "EX-R-14001", "title": "예외"}
    start = {"type": "step_opened", "decision": {"kind": "instruct", "section": "4.3"}, "exception_guide": eg}
    ev = [opened("1.2", "lock"), opened("4.2", "precheck"), start]
    walk = [opened("4.6"), opened("4.4", "call"), opened("4.4"), opened("4.5"), opened("EX-R-14001")]
    seen = []
    for e in [None, *walk]:
        if e:
            ev.append(e)
        seen.append(screen.check_progress(ev, k, "CL", g, set()))
    assert [x["check_no"] for x in seen] == [1, 2, 2, 3, 4, 5]
    assert {x["check_max"] for x in seen} == {5}  # 매뉴얼 넷 + 예외 하나 — 처음부터 끝까지 같은 수


def test_evidence_records_are_top_confirmed_of_that_cause(k):
    """결정 014 ③: 상위 10건 중 그 점검의 연결 원인으로 확정된 기록, 유사도 순 최대 2건(없으면 빈 목록)."""
    sim = [
        {"record_id": "H-0001", "cause": "CL-2", "score": 0.95},
        {"record_id": "H-0002", "cause": "CL-1", "score": 0.80},
        {"record_id": "H-0003", "cause": "CL-1", "score": 0.91},
        {"record_id": "H-0004", "cause": None, "score": 0.99},
        {"record_id": "H-0005", "cause": "CL-1", "score": 0.70},
    ]
    assert screen.evidence_record_ids(k, "4.3", sim) == ["H-0003", "H-0002"]  # 4.3 → CL-1
    assert screen.evidence_record_ids(k, "4.4", sim) == ["H-0001"]
    assert screen.evidence_record_ids(k, "4.5", sim) == []
    assert screen.evidence_record_ids(k, "4.2", sim) == []  # 사전 확인은 원인을 가르지 않음


def test_cause_names_are_manual_cause_table(k, data_dir_module):
    """A-04: 순위 원인 이름 = 매뉴얼 원인 표 이름. 이력 기록의 원인 이름과도 같다(PBC4-A9 실행 해석 ②)."""
    sections = {s["id"]: s for s in core.read_manual(data_dir_module)}
    for group in ("TW", "PW", "CL", "LD"):
        names = screen.cause_names(k, group)
        overview = next(s for s in sections.values() if s.get("group") == group and s["id"].endswith(".1"))
        for code, name in names.items():
            assert f"| {code} | {name} |" in overview["text"]
        assert len(names) >= 3
    hist = [
        json.loads(line) for line in (data_dir_module / "history/maintenance_history.jsonl").read_text().splitlines()
    ]
    allnames = {c: n for g in ("TW", "PW", "CL", "LD") for c, n in screen.cause_names(k, g).items()}
    confirmed = [r["outcome"] for r in hist if r["outcome"].get("cause_code")]
    assert confirmed and all(allnames[o["cause_code"]] == o["cause_name"] for o in confirmed)


SNAP = {
    "air_temperature_k": 302.7,
    "process_temperature_k": 310.5,
    "rotational_speed_rpm": 1511,
    "torque_nm": 37.7,
    "tool_wear_min": 60,
    "temp_diff_k": 7.8,
    "power_w": 5965,
}


def test_sensor_table_cl_matches_prototype_t03(table):
    """T03 / default: 알람 센서 + 4개, 평소 범위 = train 행 2.5 ~ 97.5 백분위, 알람 센서는 경계 쪽 한쪽 범위."""
    snap = {
        **SNAP,
        "air_temperature_k": 304.2,
    }  # 공기 온도를 train 평소 범위(296.7 ~ 303.8) 밖에 둠(EV-001 분할 v2 — 범위가 바뀌어 302.7은 안쪽)
    alarm = alarm_rules.detect(snap, table)
    assert alarm["group"] == "CL"
    rows = screen.sensor_rows(alarm["alarm"], "CL", snap, table)
    got = [(r["name"], r["value_text"], r["range_text"], r["status"]) for r in rows]
    assert got == [
        ("온도 차", "7.8 K", "8.2 K 이상", "경계 아래"),
        ("회전 속도", "1,511 rpm", "1,308 ~ 1,981", "정상"),
        ("회전력", "37.7 Nm", "20.3 ~ 60.1", "정상"),
        ("공구 마모", "60 분", "4 ~ 215", "정상"),
        ("공기 온도", "304.2 K", "296.7 ~ 303.8", "정상"),  # 경계 없는 센서는 평소 범위 밖이어도 '정상'
    ]
    assert screen.out_of_range_count(snap, table) == 1


def test_sensor_table_pw_alarm_sensor_plus_four(table):
    """PBC4-A8 실행 해석 ①: PW 알람 = 기계 출력 · 회전 속도 · 회전력 · 공구 마모 · 공기 온도(온도 차 빠짐), 5행."""
    snap = {**SNAP, "temp_diff_k": 9.5, "power_w": 9000}
    alarm = alarm_rules.detect(snap, table)
    assert alarm["group"] == "PW"
    rows = screen.sensor_rows(alarm["alarm"], "PW", snap, table)
    assert [r["name"] for r in rows] == ["기계 출력", "회전 속도", "회전력", "공구 마모", "공기 온도"]
    assert rows[0]["range_text"] == "8,403 W 이하" and rows[0]["status"] == "경계 위" and rows[0]["is_alarm_sensor"]


def test_sensor_table_counts_other_boundaries(table):
    snap = {**SNAP, "torque_nm": 61.0}  # 온도 차(알람) + 회전력 경계 위
    alarm = alarm_rules.detect(snap, table)
    rows = {r["name"]: r for r in screen.sensor_rows(alarm["alarm"], alarm["group"], snap, table)}
    assert rows["회전력"]["status"] == "경계 위" and rows["회전력"]["out_of_range"]
    assert screen.out_of_range_count(snap, table) == 2


def test_procedure_steps_and_criterion(k):
    assert screen.procedure_steps(k, "1.2")[0] == "운전자에게 정지를 알리고 가공 중인 프로그램을 끝낸다."
    assert len(screen.procedure_steps(k, "1.2")) == 4
    check = screen.procedure_steps(k, "4.3")  # 점검 방법만(조치 4.3.2 단계는 넣지 않음)
    assert len(check) == 4 and check[-1] == "커버를 닫고 나사를 조인다."
    assert len(screen.procedure_steps(k, "4.2")) == 2
    assert screen.procedure_steps(k, "4.3.2")[0] == "흡기구 덮개를 열고 필터를 빼낸다."
    assert screen.criterion(k, "4.3") == '기름때·먼지가 필터 표면의 절반 이상을 덮으면 "이상"이다.'
    assert screen.criterion(k, "1.2") is None


def test_action_block_carries_procedure_steps():
    """D-530 답장 001-zzy(가이드 14쪽): 조치 카드 절차 — action에 그 조치 절의 번호 줄(매뉴얼 그대로)."""
    from c1a15.api import responses

    assert responses.ActionBlock.model_fields["steps"].annotation == list[str]


def test_final_report_carries_cause_basis_and_author_role():
    """D-530 답장 001-zzx-2(가이드 20 · 21쪽): 최종본 확정 근거(점검 결과 · 메모) · 작성 근무."""
    from c1a15.api import responses

    fields = responses.FinalReport.model_fields
    assert "cause_basis" in fields and "author_role" in fields


def test_exception_record_input_and_blocks():
    """D-538 · D-566: 예외 상황 기록 = 글 하나(2000자) + 예외 전체에 한 번 같음 / 아님(약속 v10 exception_review) ·
    「AI 나눔」 · 비슷한 것 · 자동 상황 · 결과 · 자동 정보 — 확정 입력 · 보고서 · 최종본 · 목록 꼴."""
    from c1a15.api import responses, schemas

    same = {"decision": "same", "match": {"kind": "예외", "no": "EX-R-1"}}
    body = schemas.EventBody(seq=1, type="report_confirm", exception_record={"text": "가"}, exception_review=same)
    assert body.exception_record.text == "가"
    assert (body.exception_review.decision, body.exception_review.match.no) == ("same", "EX-R-1")
    assert (
        schemas.EventBody(
            seq=1, type="exception_apply", exception_record={"text": "가"}, exception_review={"decision": "not"}
        ).exception_review.match
        is None
    )
    for bad in ({"decision": "같음"}, {**same, "match": {"kind": "그래프", "no": "X"}}, {"item": 0, **same}, [same]):
        with pytest.raises(ValueError):  # 모르는 값 · v9 item 칸 · 목록 꼴은 받지 않음
            schemas.EventBody(seq=1, type="report_confirm", exception_review=bad)
    with pytest.raises(ValueError):
        schemas.ExceptionInput(text="가", choices=[])  # D-556 1단계 임시 이름은 받지 않음
    with pytest.raises(ValueError):
        schemas.ExceptionInput(text="가" * 2001)
    with pytest.raises(ValueError):
        schemas.ExceptionInput(situation="가")  # D-556 전 두 글 · 칸은 받지 않음
    assert schemas.EventBody(seq=1, type="exception_split", exception_text="가").exception_text == "가"
    with pytest.raises(ValueError):
        schemas.EventBody(seq=1, type="exception_split")  # 나눌 글 없으면 422
    assert set(responses.ExceptionDraft.model_fields) == {"text"}
    assert {"situation", "text", "exception", "result", "action", "context"} <= set(
        responses.ExceptionRecord.model_fields
    )
    assert "items" not in responses.ExceptionRecord.model_fields  # v9 짝 기록은 예외 하나로 읽음
    assert set(responses.ExceptionOne.model_fields) == {
        "topic",
        "checks",
        "actions",
        "matches",
        "edit",  # 정답지 30쪽 같음 + 고침(앱 임시 칸)
        "decision",
        "match",
        "kind",
    }
    assert set(schemas.ExceptionEdit.model_fields) == {"checks", "actions"}  # same-edit.md 1절
    with pytest.raises(ValueError):
        schemas.ExceptionEdit(checks=[], actions=[{"what": "가"}])  # 목록 1 ~ 10
    assert "edit" in responses.ExceptionReviewOut.model_fields
    assert {"checks", "actions"} <= set(responses.ExceptionSimilar.model_fields)  # 예외 줄 지금 목록(선택)
    assert set(responses.ExceptionReviewOut.model_fields) == {
        "decision",
        "match",
        "edit",
    }  # 승인 기록 파일 칸(약속 v10 6절)
    assert "exception_review" in responses.ExceptionRecord.model_fields
    assert set(responses.ExceptionSplit.model_fields) == {"text", "exception", "error", "similar_error"}
    assert {"kind", "no", "name", "score"} <= set(responses.ExceptionSimilar.model_fields)  # 약속 v10 3절
    assert set(responses.CheckRecord.model_fields) == {
        "kind",
        "session_id",
        "human_check",
        "relation_id",
        "at_seq",
        "how",  # D-633 안 고름 줄 몫(그 밖 줄은 null)
        "run_id",
    }
    assert {"exception_auto", "exception_split"} <= set(responses.ReportBlock.model_fields)
    assert (
        "exception_record" in responses.ReportBlock.model_fields
        and "exception_record" in responses.FinalReport.model_fields
    )
    draft = schemas.EventBody(seq=1, type="exception_draft", exception_record={"text": "가"})
    assert draft.exception_record.text == "가"
    with pytest.raises(ValueError):
        schemas.EventBody(seq=1, type="exception_draft")  # D-543 「노하우 등록」: 글 칸 없으면 422
    assert (
        "exception_notes" in responses.SessionView.model_fields
        and "exception_draft" in responses.ReportBlock.model_fields
    )
    assert set(responses.ExceptionContext.model_fields) == {
        "equipment_id",
        "symptom_group",
        "occurred_at",
        "sensor_values",
        "previous_maintenance",
        "recent_maintenance",  # D-570 최근 N일 정비 이력 전부
        "ai_first_check",
        "confirmed_cause",
        "report_id",
    }
    assert set(responses.ExceptionRecent.model_fields) == {
        "record_id",
        "occurred_at",
        "days_before",
        "did",
        "checked",
        "outcome",
    }


def test_lock_instruction_is_only_section_1_2(k, grades):
    out = screen.lock_instruction(k, *grades)
    assert set(out) == {"section", "title", "grade", "minutes", "steps"}
    assert out["section"] == "1.2" and out["title"] == "잠금 확인" and len(out["steps"]) == 4


def test_ai_reading_is_grounded_or_falls_back(table, monkeypatch):
    """T03 AI 해석: fixture 문장은 R8 통과(수치 = 값 · 경계), 근거 없는 문장은 코드 템플릿 + '원문으로 대체됨'."""
    alarm = alarm_rules.detect(SNAP, table)["alarm"]
    r = screen.ai_reading(alarm, "temp_diff_k", "온도 차 저하", "냉각 계통")
    assert not r["fallback"] and r["label"] is None and "7.8 K" in r["text"] and "냉각 계통" in r["text"]

    class Ungrounded:
        mode = "x"

        def write(self, facts):
            return Draft("온도 차가 4.4절 기준으로 12% 낮습니다.")

    monkeypatch.setattr(screen, "adapter_for_mode", lambda: Ungrounded())
    r = screen.ai_reading(alarm, "temp_diff_k", "온도 차 저하", "냉각 계통")
    assert r["fallback"] and r["label"] == "원문으로 대체됨" and "4.4" not in r["text"] and "12" not in r["text"]
