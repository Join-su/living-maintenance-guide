# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-619 사전 점검 후보 한 줄기(DB · 가짜 배포): 예시 줄 → 홈 B · C → 승인 → 새 revision 뒤 A → 기록만."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from test_api import TECH_PW, api, db, login, register, reset_ops  # noqa: F401
from test_deploy_path import deploy  # noqa: F401
from test_exception_guide import _approved_exception

pytestmark = pytest.mark.db


def _line(rid, eq, days_ago, group, *, did=(), bad=(), topic=None):
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=days_ago)
    return {
        "record_id": rid,
        "equipment_id": eq,
        "occurred_at": at.isoformat(),
        "symptom_group": {"code": group, "name": group},
        "sensor_snapshot": {},
        "steps": [
            *({"kind": "check", "section": s, "result": "이상"} for s in bad),
            *({"kind": "action", "section": s, "name": "흡기 필터 교체"} for s in did),
        ],
        "outcome": {"type": "원인 미상·숙련자 인계"},
        "narrative": f"{eq} 예시",
        **({"exception_record": {"text": topic}} if topic else {}),
    }


def _pattern_lines():
    rows, n = [], 0

    def add(*a, **k):
        nonlocal n
        n += 1
        rows.append(_line(f"H-P{n:03d}", *a, **k))

    for eq, days in (("M-05", 90), ("M-06", 80)):
        add(eq, days, "CL", did=["4.3.2"])
        add(eq, days - 5, "CL", topic="필터 덮개 고정")
    add("M-07", 50, "CL", did=["4.3.2"])
    add("M-08", 40, "CL", did=["4.3.2"])
    add("M-04", 2, "CL", did=["4.3.2"])
    for eq, days in (("M-09", 90), ("M-10", 80), ("M-11", 70)):
        add(eq, days, "TW", did=["2.4.2"])
        add(eq, days - 12, "TW", bad=["2.3"])
    add("M-12", 50, "TW", did=["2.4.2"])
    add("M-14", 40, "TW", did=["2.4.2"])
    add("M-04", 3, "TW", did=["2.4.2"])
    return rows


def _pattern_c_lines():
    """D-653 시연 예시 = 패턴 C(엔코더 커넥터 재체결 3.4.2 → 7일 안 회전 조건 설정 확인 3.3 이상)만 · 예시 대본과 같은 번호."""
    rows, n = [], 16

    def add(*a, **k):
        nonlocal n
        n += 1
        rows.append(_line(f"H-P{n:03d}", *a, **k))

    for eq, days in (("M-15", 60), ("M-16", 53), ("M-17", 54)):
        add(eq, days, "PW", did=["3.4.2"])
        add(eq, days - 5, "PW", bad=["3.3"])
    add("M-18", 53, "PW", did=["3.4.2"])
    add("M-19", 33, "PW", did=["3.4.2"])
    add("M-04", 3, "PW", did=["3.4.2"])
    return rows


@pytest.fixture
def lines(db):
    from c1a15 import demo_history

    reset_ops(db)
    with db.begin() as c:
        demo_history.precheck_remove(c)
        demo_history.remove(c, "H-0301")
        demo_history.add(
            c,
            {
                **_line("H-0301", "M-01", 3, "CL", did=["4.3.2"]),
                "provenance": "시연 이력 줄(D-570)",
                "demo": "D-570",
            },
        )
        demo_history.precheck_add(c, _pattern_lines())
    yield
    with db.begin() as c:
        demo_history.precheck_remove(c)
        demo_history.remove(c, "H-0301")
        c.execute(text("delete from knowledge.history_records where source = 'approved_report'"))


def _fresh():
    from c1a15.deploy import runs

    runs._REVISION.update(at=None, value=None)


def _home(api, user="tech.jung"):
    _fresh()
    tech, _ = login(api, "tech", user, TECH_PW)
    return tech, tech.get("/api/tech/home").json()["prechecks"]


def test_b_card_only_a_waits_for_the_deployed_graph_then_shows_five_three(api, db, deploy, lines):
    _, cards = _home(api)
    assert [(x["pattern"], x["equipment_id"], x["total"], x["hits"], x["clean"]) for x in cards] == [
        ("B", "M-04", 5, 3, 2)
    ]
    _approved_exception(api, db)
    with db.connect() as c:
        got = c.execute(
            text("select detail->>'deploy_revision' from ops.audit_log where action = 'report_approve'")
        ).scalar()
    assert got == "sha256:fake-first"
    _, cards = _home(api)
    assert [x["pattern"] for x in cards] == ["B"]  # 승인만으로는 안 뜸

    deploy.revision = "sha256:fake-next"
    _, cards = _home(api)
    a = next(x for x in cards if x["pattern"] == "A")
    assert (a["equipment_id"], a["total"], a["hits"], a["clean"], a["days_ago"]) == ("M-04", 5, 3, 2, 2)
    assert (a["section"], a["section_title"], a["within_days"]) == ("4.3.2", "흡기 필터 교체", 7)
    assert [b["result"] for b in a["basis"]].count("이어짐") == 3
    assert len(a["basis"]) == 5


def test_lead_overview_shows_every_card_with_owner_without_switching_user(api, db, deploy, lines):
    """D-642 E(R8 · R11): 파트장 승인 대기 '사전 점검 후보' 칸 = 담당자 홈과 같은 셈 · 모든 담당 설비 · 담당자 이름.
    그래프 반영(배포 revision 바뀜) 뒤 다음 조회에 A가 뜸 — 사용자 바꾸기 없이(파트장 앱은 10초마다 다시 읽음)."""
    from test_lead import lead

    lc = lead(api)
    with db.connect() as c:
        jung = c.execute(text("select display_name from ops.accounts where user_id = 'tech.jung'")).scalar()

    def rows():
        _fresh()
        return lc.get("/api/lead/overview").json()["prechecks"]

    assert [(x["pattern"], x["equipment_id"], x["assignee_name"], x["total"], x["hits"]) for x in rows()] == [
        ("B", "M-04", jung, 5, 3)
    ]
    _approved_exception(api, db)
    assert [x["pattern"] for x in rows()] == ["B"]  # 승인만으로는 안 뜸(담당자 홈과 같음)
    deploy.revision = "sha256:fake-next"
    got = rows()
    a = next(x for x in got if x["pattern"] == "A")
    assert (a["equipment_id"], a["assignee_name"], a["total"], a["hits"], a["look_at"]) == (
        "M-04",
        jung,
        5,
        3,
        "필터 덮개 고정",
    )
    assert [(x["pattern"], x["record_id"]) for x in got] == [(x["pattern"], x["record_id"]) for x in _home(api)[1]]


def test_only_the_owner_sees_it_and_an_alarm_since_the_action_removes_it(api, db, deploy, lines):
    _, cards = _home(api, "tech.kim")
    assert cards == []
    assert [x["pattern"] for x in _home(api)[1]] == ["B"]
    assert register(api, "M-04").status_code in (200, 201)
    assert _home(api)[1] == []  # 앞 조치 뒤 알람이 있던 설비는 뺌


def test_look_and_skip_only_leave_a_record_and_counts_stay(api, db, deploy, lines):
    tech, cards = _home(api)
    b = cards[0]
    url = f"/api/tech/prechecks/B/{b['record_id']}"
    assert tech.post(url, json={"choice": "skip"}).status_code == 422
    assert tech.post(url, json={"choice": "skip", "reason": "  "}).status_code == 422
    assert tech.post(url, json={"choice": "skip", "reason": "이번 주 교체 예정"}).status_code == 200
    assert tech.post(url, json={"choice": "look"}).status_code == 200  # 한 건에 기록 하나
    after = tech.get("/api/tech/home").json()["prechecks"][0]
    assert (after["total"], after["hits"], after["clean"]) == (b["total"], b["hits"], b["clean"])
    assert (after["mark"]["choice"], after["mark"]["by"], after["mark"]["reason"]) == (
        "skip",
        "tech.jung",
        "이번 주 교체 예정",
    )
    with db.connect() as c:
        rows = c.execute(
            text(
                "select actor, action, target, equipment_id from ops.audit_log where action in ('precheck_look', 'precheck_skip')"
            )
        ).all()
    assert [tuple(r) for r in rows] == [("tech.jung", "precheck_skip", f"precheck:B:{b['record_id']}", "M-04")]
    assert tech.post("/api/tech/prechecks/B/H-0001", json={"choice": "look"}).status_code == 404


def test_remove_takes_only_marked_lines(db, lines):
    from c1a15 import demo_history

    with db.begin() as c:
        before = c.execute(text("select count(*) from knowledge.history_records")).scalar()
        n = demo_history.precheck_remove(c)
        after = c.execute(text("select count(*) from knowledge.history_records")).scalar()
    assert n == len(_pattern_lines()) and before - after == n


def _lead_cards(api):
    from test_api import LEAD_PW

    _fresh()
    lead, _ = login(api, "lead", "lead.park", LEAD_PW)
    return lead.get("/api/lead/overview").json()["prechecks"]


def test_lead_overview_recounts_on_the_graph_signal_without_switching_user(api, db, deploy, lines):
    """D-642 E: 파트장 앱 '사전 점검 후보' 칸 — 담당자와 무관하게 모든 설비 후보 · 기계 · 담당자 · 5건 중 3건.
    승인만으로는 그대로 · 그래프 반영 신호(배포 revision 바뀜)가 오면 다음 조회에서 A가 들어옴(사용자 바꾸기 0)."""
    with db.connect() as c:
        owner = c.execute(
            text(
                "select a.user_id, u.display_name from ops.equipment_assignments a join ops.accounts u using (user_id) "
                "where a.equipment_id = 'M-04'"
            )
        ).one()
    rows = _lead_cards(api)
    assert [(x["pattern"], x["equipment_id"], x["assignee"], x["total"], x["hits"]) for x in rows] == [
        ("B", "M-04", owner.user_id, 5, 3)
    ]
    assert rows[0]["assignee_name"] == owner.display_name
    _approved_exception(api, db)
    assert [x["pattern"] for x in _lead_cards(api)] == ["B"]
    deploy.revision = "sha256:fake-next"
    rows = _lead_cards(api)
    a = next(x for x in rows if x["pattern"] == "A")
    assert (a["equipment_id"], a["assignee"], a["total"], a["hits"], a["clean"]) == ("M-04", owner.user_id, 5, 3, 2)
    assert a["assignee_name"] == owner.display_name


def test_tech_home_card_says_whose_machine_it_is(api, db, deploy, lines):
    """D-642 E ③: 정비자 홈 카드는 담당 설비 것만 그대로 + 담당 표시 칸."""
    _, cards = _home(api)
    assert [(x["pattern"], x["assignee"]) for x in cards] == [("B", "tech.jung")]
    assert cards[0]["assignee_name"]


def test_d653_c_only_lines_kim_sees_c_from_first_state_and_no_a_after_reflect(api, db, deploy):
    """D-653(D-650 덧1 A안 + R4 위): 시연 첫 상태 = 패턴 C 예시 줄만 · M-04 담당 = 김 정비 → 첫 상태부터 김 정비 홈 C 1장 ·
    M-01 예외 승인 → 새 배포 뒤에도 A 0(덮개 이어짐 M-01 한 건 < 3) · C 그대로 · 파트장 칸도 1건."""
    from c1a15 import demo_history

    reset_ops(db)
    c_only = _pattern_c_lines()  # D-653: 시연 예시 = 패턴 C만
    with db.begin() as c:
        demo_history.precheck_remove(c)
        demo_history.remove(c, "H-0301")
        demo_history.add(
            c, {**_line("H-0301", "M-01", 3, "CL", did=["4.3.2"]), "provenance": "시연 이력 줄(D-570)", "demo": "D-570"}
        )
        demo_history.precheck_add(c, c_only)
        old = c.execute(text("select user_id from ops.equipment_assignments where equipment_id = 'M-04'")).scalar()
        c.execute(text("update ops.equipment_assignments set user_id = 'tech.kim' where equipment_id = 'M-04'"))
    try:
        assert [(x["pattern"], x["equipment_id"], x["total"], x["hits"]) for x in _home(api, "tech.kim")[1]] == [
            ("C", "M-04", 5, 3)
        ]
        assert _home(api, "tech.jung")[1] == []
        _approved_exception(api, db)
        deploy.revision = "sha256:fake-next"
        kim = _home(api, "tech.kim")[1]
        assert [(x["pattern"], x["equipment_id"]) for x in kim] == [("C", "M-04")]  # A 0
        assert [(x["pattern"], x["assignee"]) for x in _lead_cards(api)] == [("C", "tech.kim")]
    finally:
        with db.begin() as c:
            c.execute(text("update ops.equipment_assignments set user_id = :u where equipment_id = 'M-04'"), {"u": old})
            demo_history.precheck_remove(c)
            demo_history.remove(c, "H-0301")
            c.execute(text("delete from knowledge.history_records where source = 'approved_report'"))


M02_CL4 = {"H-0305": 30, "H-0306": 37}


def _pattern_f_lines():
    """D-656 시연 예시 = 패턴 F(컨트롤러 펌웨어 업데이트 7.1 · PW → 7일 안 냉각 설정 확인 4.6 이상)만 · 예시 대본 번호:
    다른 설비 넷(이어짐 둘 · 이상 없음 둘 → 2/4 · 기준 미만) · M-02 업데이트 5일 전 · M-04 업데이트 3일 전."""
    rows, n = [], 25

    def add(*a, fw=False, **k):
        nonlocal n
        n += 1
        row = _line(f"H-P{n:03d}", *a, **k)
        if fw:
            row["narrative"] = f"{row['equipment_id']} 컨트롤러 펌웨어 업데이트 — 전기 담당 작업(7.1 전기 계통 작업)."
        rows.append(row)

    for eq, days in (("M-05", 60), ("M-06", 61)):
        add(eq, days, "PW", did=["7.1"], fw=True)
        add(eq, days - 5, "CL", bad=["4.6"])
    add("M-07", 52, "PW", did=["7.1"], fw=True)
    add("M-08", 33, "PW", did=["7.1"], fw=True)
    add("M-02", 5, "PW", did=["7.1"], fw=True)
    add("M-04", 3, "PW", did=["7.1"], fw=True)
    return rows


def _m02_plain_report_approved(api, db):
    """M-02 보통 처리 끝까지(예외 아님 · AI 나눔 0): 4.3 정상(헛걸음) → 4.6 이상 → 조치 → 재가동 → 확정 → 파트장 승인."""
    from test_api import ok, sim_answer, start, to_instruct
    from test_exception_record import _rid
    from test_lead import lead

    tech, s = start(api, db, user="tech.kim", equipment="M-02")
    s = to_instruct(tech, s)
    seen = []
    for _ in range(12):  # 4.3 정상 → 4.6 이상 → (C 조치면 파트장 호출 · 승인 후 진행) → 조치
        kind = s["step"]["kind"]
        if kind == "action":
            break
        if kind == "call":
            sec = s["call"]["section"]
            s = ok(tech, s, type="call_send")
            sim_answer(db, s["session_id"], "approved", sec)
            s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
            seen.append(f"call {sec}")
            continue
        sec = s["instruction"]["section"]
        seen.append(sec)
        s = ok(tech, s, type="result", section=sec, value="이상" if sec == "4.6" else "정상")
    assert s["step"]["kind"] == "action", (seen, s["step"])
    s = ok(tech, s, type="action_done", section=s["step"]["section"])
    s = ok(tech, s, type="restart", checked=s["restart"]["checklist"])
    s = ok(tech, s, type="restart_result", outcome="within")
    s = ok(tech, s, type="report_confirm")
    rid = _rid(db, s["session_id"])
    got = lead(api).post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve"})
    assert got.status_code == 200, got.text
    return seen, got.json()["history_record_id"]


def test_d656_f_card_zero_at_first_state_then_one_right_after_m02_plain_approval(api, db, deploy):
    """D-656: 첫 상태 = 패턴 F 예시 줄만(C 0) · M-04 담당 김 정비 → 카드 0(2/4) · 파트장 칸 0 →
    M-02 보통 보고서 승인(4.3 헛걸음 → 4.6 이상) → 배포 revision 그대로(그래프 기다림 0)인데 김 정비 홈 F 1장 · 5건 중 3건 · 파트장 칸 1건."""
    from c1a15 import demo_history

    reset_ops(db)
    with db.begin() as c:
        demo_history.precheck_remove(c)
        demo_history.remove(c, "H-0301")
        demo_history.precheck_add(c, _pattern_f_lines())
        for r in M02_CL4:
            demo_history.remove(c, r)
        old = c.execute(text("select user_id from ops.equipment_assignments where equipment_id = 'M-04'")).scalar()
        c.execute(text("update ops.equipment_assignments set user_id = 'tech.kim' where equipment_id = 'M-04'"))
    for r, days in M02_CL4.items():  # 시연 첫 상태와 같은 명령 · 자료(new-pack 013-280 ④ M-02 지난 CL-4 2건)
        demo_history.main(
            [
                "add",
                "--record",
                r,
                "--equipment",
                "M-02",
                "--group",
                "CL",
                "--days-ago",
                str(days),
                "--check",
                "4.6",
                "--action",
                "4.6.2",
            ]
        )
    try:
        assert _home(api, "tech.kim")[1] == []
        assert _lead_cards(api) == []
        seen, hid = _m02_plain_report_approved(api, db)
        assert seen[0] == "4.3" and "4.6" in seen, (
            seen
        )  # 4.3 헛걸음 · 4.6 이상(4.3 바로 다음 4.6은 실제 Pack 몫 · 리허설에서 봄)
        with db.connect() as c:
            rec = c.execute(
                text("select record from knowledge.history_records where record_id = :r"), {"r": hid}
            ).scalar()
        assert "exception_record" not in rec
        kim = _home(api, "tech.kim")[1]  # deploy.revision 그대로
        assert [(x["pattern"], x["equipment_id"], x["total"], x["hits"], x["clean"], x["days_ago"]) for x in kim] == [
            ("F", "M-04", 5, 3, 2, 3)
        ]
        f = kim[0]
        assert (f["within_days"], f["follow"], f["record_id"]) == (7, "냉각 설정 오류", "H-P033")
        assert (f["section"], f["section_title"], f["look_at"]) == ("7.1", "컨트롤러 펌웨어 업데이트", "냉각 설정값")
        assert (f["act_short"], f["follow_short"]) == ("펌웨어 업데이트", "설정 오류")  # D-670 카드 펼침 한 줄
        assert any(
            b["equipment_id"] == "M-02" and b["result"] == "이어짐" for b in f["basis"]
        )  # 방금 승인한 M-02가 셋째
        assert [(x["pattern"], x["assignee"]) for x in _lead_cards(api)] == [("F", "tech.kim")]
    finally:
        with db.begin() as c:
            c.execute(text("update ops.equipment_assignments set user_id = :u where equipment_id = 'M-04'"), {"u": old})
            demo_history.precheck_remove(c)
            for r in M02_CL4:
                demo_history.remove(c, r)
            c.execute(text("delete from knowledge.history_records where source = 'approved_report'"))
