# ruff: noqa: E501, F811 — 시험 표의 메모 예문은 길이 제한 밖 · test_api · test_deploy_path fixture를 이름으로 씀
"""D-301 (4) · 001-63: 정비자 메모 속 사람 이름 거르기 — 앱 밖으로 나가는 글만 [이름](화면 · 앱 DB는 원문).

출구 넷: 판정 Run 메모 인용 · 보고서 Run 초안 · R12 받은 편지 글 · live 직접 호출 문장.
c1a15-pack 동의 조건(PR-16): 기록 ID(H-/R-nnnn) · 절 번호 · 수치는 그대로.
"""

import pytest

from c1a15 import redact
from c1a15.deploy import inbox, runs
from c1a15.llm.base import Facts
from c1a15.llm.live import LiveAdapter
from test_api import TECH_PW, api, db, login, ok, register, to_instruct  # noqa: F401
from test_deploy_path import count, deploy, reset_ops  # noqa: F401

MEMO = "김 반장이 확인, 기름때 절반 · tech.lee 연락 010-1234-5678"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("김 정비가 열어 봄", "[이름]가 열어 봄"),  # 계정 이름표
        ("박 파트장께 보고", "[이름]께 보고"),
        ("tech.kim · lead.park 확인", "[이름] · [이름] 확인"),  # 계정 ID 꼴
        ("최반장 입회", "[이름] 입회"),  # 성 + 직함(붙여 씀)
        ("한 주임이 커버 닫음", "[이름]이 커버 닫음"),
        ("김민수 반장 확인", "[이름] 확인"),  # 성명 + 직함
        ("민수 씨가 봄 · 이민수님 입회", "[이름]가 봄 · [이름] 입회"),  # 이름 + 씨 · 님
        ("연락 010-1234-5678", "연락 [이름]"),  # 전화
    ],
)
def test_redacts_names(text, want):
    assert redact.redact(text)[0] == want


@pytest.mark.parametrize(
    "text",
    [
        "이상 있음 · 정상 아님 · 조치 뒤 고온 · 한쪽만 · 공기 사용",  # 성 한 글자로 시작하는 낱말
        "H-0141 · R-0007 · RPT-000012 · 4.3절 · 4.3.2 · 46분 · 7.8 K · 1,409 rpm",  # 기록 ID · 절 · 수치(PR-16 동의 조건)
        "이상을 정비 기록에 남김 · 차례면 파트장에게 넘김",  # 조사로 끝난 낱말 + 직함(공유 trace 읽기 대조에서 찾은 꼴)
        "[이름] 확인",  # 이미 거른 글(두 번 걸러도 같음)
        "",
    ],
)
def test_keeps_record_ids_sections_numbers_and_words(text):
    assert redact.redact(text) == (text, 0)


def test_count_and_nested_objects():
    obj = {"a": MEMO, "b": ["H-0141 · 4.3", {"c": "정 정비"}], "n": 3, "none": None}
    out, n = redact.redact_obj(obj)
    assert n == 4
    assert out == {
        "a": "[이름]이 확인, 기름때 절반 · [이름] 연락 [이름]",
        "b": ["H-0141 · 4.3", {"c": "[이름]"}],
        "n": 3,
        "none": None,
    }
    assert obj["a"] == MEMO  # 원본은 그대로(화면 · 앱 DB 몫)


def test_exit1_judge_quote():
    ev = runs.judge_events(
        [
            {
                "seq": 3,
                "type": "result",
                "section": "4.3",
                "value": "이상",
                "memo": MEMO,
                "signal": {"quote": MEMO[:40], "out_of_scope_finding": False},
            }
        ]
    )
    assert ev[0]["signal"]["quote"].startswith("[이름]이 확인") and "김 반장" not in str(ev)
    assert "memo" not in ev[0]  # 메모 원문 칸은 원래 보내지 않음(VC-35)


def test_exit2_report_draft():
    draft = {
        "check_path": f"흡기 필터 육안 점검(4.3, 이상: {MEMO[:30]}…)",
        "unresolved": "냉각 팬 분해 점검(4.4, C등급)",
        "evidence": "H-0197 · H-0232",
    }
    body = runs.report_body(7, "CL", [{"record_id": "H-0197", "cause": "CL-1", "score": 0.78}], draft, ["4.3"])
    assert (
        body["report_draft"]["check_path"]
        == "흡기 필터 육안 점검(4.3, 이상: [이름]이 확인, 기름때 절반 · [이름] 연락…)"
    )
    assert body["report_draft"]["evidence"] == "H-0197 · H-0232" and body["similar"][0]["record_id"] == "H-0197"
    assert draft["check_path"].startswith("흡기 필터 육안 점검(4.3, 이상: 김 반장")  # 넘긴 초안(앱 DB 몫)은 그대로


def test_exit3_inbox_text():
    rec = {
        "sensor_snapshot": {k: 1.0 for k in inbox.SENSORS7},
        "symptom_group": {"code": "CL"},
        "outcome": {"cause_code": "CL-1"},
        "source": {"report_id": 3},
        "record_id": "R-0003",
        "narrative": "M-01 · 온도 차 저하. 흡기 필터 육안 점검(4.3, 이상: 최 주임 입회 기름때). 냉각 필터 막힘로 판단.",
    }
    d = inbox.document(rec, "t")["document"]
    assert (
        d["text"] == "M-01 · 온도 차 저하. 흡기 필터 육안 점검(4.3, 이상: [이름] 입회 기름때). 냉각 필터 막힘로 판단."
    )
    assert d["record_id"] == "R-0003" and rec["narrative"].count("최 주임") == 1


def test_exit4_live_prompt():
    sent = []

    class Client:
        class responses:  # noqa: N801 — openai client 모양
            @staticmethod
            def create(model, input):
                sent.append(input)
                return type("R", (), {"output_text": "문장"})()

    LiveAdapter(Client(), "m").write(Facts("handover", "4.3", {"body": f"흡기 필터(4.3). 소견: {MEMO[:30]}…"}))
    assert "김 반장" not in sent[0] and "tech.lee" not in sent[0] and "[이름]" in sent[0] and "4.3" in sent[0]


@pytest.mark.db
def test_judge_run_body_and_audit_have_no_names(api, db, deploy):
    """앱 → 가짜 배포 서버: 버튼과 반대 메모(정상 + 기름때)의 인용이 판정 Run에 [이름]으로 · 감사엔 거른 수만 · 앱 DB 원문 그대로."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = to_instruct(tech, tech.post(f"/api/tech/alarms/{register(api, 'M-01').json()['alarm_id']}/session").json())
    memo = "김 반장 입회, 기름때가 필터 절반 덮음"
    ok(tech, s, type="result", section=s["instruction"]["section"], value="정상", memo=memo)
    quotes = [
        e["signal"]["quote"]
        for p in deploy.posts()
        if p["body"]["kind"] == "judge"
        for e in p["body"]["events"]
        if "signal" in e
    ]
    assert quotes and all("김 반장" not in q and q.startswith("[이름] 입회") for q in quotes)
    assert (
        count(db, "select count(*) from ops.audit_log where action = 'names_redacted' and detail->>'where' = 'judge'")
        >= 1
    )
    assert (
        count(db, "select count(*) from ops.audit_log where action = 'names_redacted' and detail::text like '%반장%'")
        == 0
    )
    assert count(db, "select count(*) from ops.session_events where payload->>'memo' = :m", m=memo) == 1  # 앱 DB 원문
