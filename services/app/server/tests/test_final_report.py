# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-528 · D-530 덧 4: 보고서 최종본 읽기(/api/lead/reports/{id}/final) — 구역 1 ~ 5 값이 업무 기록 그대로인지(시험 DB).

진단 때 % · 근거 n = 정비자 '지난 화면' 진단 단계와 같은 값(preview_shown), 권한 사용 = 조치 경과 줄 등급 셈. 새 저장 0.
FINAL_FIXTURE_OUT을 주면 실제 응답 한 건을 그 파일로 남긴다(파트장 화면 시험 fixture — 실제 출력 꼴, D-503 2).
"""

import json
import os

import pytest
from sqlalchemy import text

from test_api import TECH_PW, api, db, login, ok, reset_ops  # noqa: F401
from test_lead import confirmed_report, lead

pytestmark = pytest.mark.db


def table_count(db, sql):
    with db.connect() as c:
        return c.execute(text(sql)).scalar()


def test_final_report_sections_match_session_records(api, db):
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    lc = lead(api)
    before = {
        t: table_count(db, f"select count(*) from {t}") for t in ("ops.reports", "ops.session_events", "ops.audit_log")
    }
    r = lc.get(f"/api/lead/reports/{rid}/final")
    assert r.status_code == 200
    f = r.json()
    if os.environ.get("FINAL_FIXTURE_OUT"):
        with open(os.environ["FINAL_FIXTURE_OUT"], "w", encoding="utf-8") as fp:
            json.dump(f, fp, ensure_ascii=False, indent=1)
    # 머리 · 진행
    assert (f["status"], f["equipment_id"], f["group_name"], f["rejected_before"], f["decision"]) == (
        "awaiting_approval",
        "M-01",
        "온도 차 저하",
        False,
        None,
    )
    assert f["confirmed_at"] and f["lock_at"] and f["author_name"] == "김 정비" and f["equipment_type"]
    assert f["author_role"] == "야간 정비"  # 답장 001-zzx-2: 머리 '작성 김 정비(야간 정비)'
    assert f["cause_basis"] == {
        "section": "4.3",
        "title": "흡기 필터 육안 점검",
        "value": "이상",
        "memo": "기름때가 필터 표면 절반 넘게 덮음",
    }
    # 1 센서 표: 알람 센서(온도 차)가 첫 줄 · 경계 넘음
    assert (
        f["sensors"][0]["name"] == "온도 차"
        and f["sensors"][0]["out_of_range"]
        and f["sensors"][0]["status"] == "경계 아래"
    )
    # 2 원인: 진단 때 % · 근거 n = 지난 화면 진단 단계 값과 같음 · 확정 원인 한 줄
    past = tech.get(f"/api/tech/sessions/{s['session_id']}/history").json()
    diag = next(x for x in past["steps"][1]["records"] if x["kind"] == "diagnosis")
    names = diag["cause_names"]
    assert {c["name"]: c["probability"] for c in f["causes"]} == {names[k]: v for k, v in diag["probabilities"].items()}
    assert {c["name"]: c["evidence"] for c in f["causes"]} == {names[k]: v for k, v in diag["evidence"].items()}
    probs = [c["probability"] for c in f["causes"]]
    assert probs == sorted(probs, reverse=True)  # 진단 때 % 높은 순(가이드 31쪽)
    hot = [c for c in f["causes"] if c["confirmed"]]
    assert [(c["name"], c["result"]) for c in hot] == [("냉각 필터 막힘", "확정 · 4.3 점검 이상")]
    assert f["cause"] == "냉각 필터 막힘" and len(f["evidence_records"].split(" · ")) == 3
    # 3 조치 경과: 사람 행동만(시스템 지시 0), 시각 순, 등급은 지금 등급표
    texts = [x["text"] for x in f["timeline"]]
    assert texts[:2] == ["점검 시작", "알람 확인"] and texts[-1] == "보고서 확정"
    assert not any(t.startswith(("지시 ·", "호출 필요")) for t in texts)
    lock = next(x for x in f["timeline"] if x["text"].startswith("잠금 완료"))
    assert (lock["section"], lock["grade"]) == ("1.2", "A")
    pre = next(x for x in f["timeline"] if x["text"] == "사전 확인 완료")
    assert (pre["section"], pre["grade"]) == ("4.2", "A")
    act = next(x for x in f["timeline"] if x["text"].startswith("조치 완료"))
    assert act["section"] == "4.3.2" and act["grade"] in {"A", "B", "C"}
    assert [x["at"] for x in f["timeline"]] == sorted(x["at"] for x in f["timeline"])
    # 4 권한 사용 = 3의 등급 셈
    assert f["grade_use"] == {g: sum(1 for x in f["timeline"] if x["grade"] == g) for g in ("A", "B", "C")}
    assert (
        f["restart"] and " · 경계 " in f["restart"] and f["restart"].endswith(" 이내") and f["downtime"]
    )  # 경계 값까지
    assert f["unresolved"]
    # 읽기만: 표 행 수 그대로
    assert {t: table_count(db, f"select count(*) from {t}") for t in before} == before


def test_final_report_after_reject_and_approve(api, db):
    """반려 → 다시 확정이면 '정비자 다시 확정', 승인 뒤 결정(파트장 · 시각) · 상태 승인 완료. 감사 행에 보고서 번호."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    lc = lead(api)
    assert (
        lc.post(
            f"/api/lead/reports/{rid}/decision", json={"decision": "reject", "memo": "4.3 메모에 필터 사진 번호"}
        ).status_code
        == 200
    )
    f = lc.get(f"/api/lead/reports/{rid}/final").json()
    assert (
        f["status"] == "rejected"
        and f["decision"]["kind"] == "rejected"
        and f["decision"]["memo"] == "4.3 메모에 필터 사진 번호"
    )
    s = tech.get(f"/api/tech/sessions/{s['session_id']}").json()
    ok(tech, s, type="report_confirm")
    f = lc.get(f"/api/lead/reports/{rid}/final").json()
    assert f["status"] == "awaiting_approval" and f["rejected_before"] and f["decision"] is None
    assert [x["text"] for x in f["timeline"]][-3:] == ["보고서 확정", "보고서 반려", "보고서 확정"]
    assert lc.post(f"/api/lead/reports/{rid}/decision", json={"decision": "approve", "memo": "확인"}).status_code == 200
    f = lc.get(f"/api/lead/reports/{rid}/final").json()
    assert f["status"] == "approved" and f["decision"]["kind"] == "approved" and f["decision"]["by_name"] == "박 파트장"
    rows = lc.get("/api/lead/audit").json()["rows"]
    linked = {r["action"]: r["report_id"] for r in rows if r["report_id"]}
    assert linked == {"보고서 확정": rid, "보고서 반려": rid, "보고서 승인": rid}


def test_final_report_role_and_missing(api, db):
    reset_ops(db)
    _, _, rid = confirmed_report(api, db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    assert tech.get(f"/api/lead/reports/{rid}/final").status_code in {401, 403}
    assert lead(api).get(f"/api/lead/reports/{rid + 999}/final").status_code == 404


def test_tech_final_report_own_only_same_values(api, db):
    """D-530 답장 001-zzq: 정비자 쪽 최종본 = 파트장 최종본과 같은 값(같은 함수), 본인 보고서만 — 남의 보고서는 403 · 거부 감사."""
    reset_ops(db)
    tech, s, rid = confirmed_report(api, db)
    assert (
        tech.get(f"/api/tech/sessions/{s['session_id']}").json()["report"]["report_id"] == rid
    )  # 「최종본 보기」 번호
    mine = tech.get(f"/api/tech/reports/{rid}/final")
    assert mine.status_code == 200
    assert mine.json() == lead(api).get(f"/api/lead/reports/{rid}/final").json()
    other, _ = login(api, "tech", "tech.lee", TECH_PW)
    r = other.get(f"/api/tech/reports/{rid}/final")
    assert r.status_code == 403 and r.json()["detail"] == "not_own_report"
    assert table_count(db, "select count(*) from ops.audit_log where detail->>'reason' = 'not_own_report'") == 1
    assert tech.get(f"/api/tech/reports/{rid + 999}/final").status_code == 404
    assert lead(api).get(f"/api/tech/reports/{rid}/final").status_code in {401, 403}
