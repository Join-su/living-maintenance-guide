# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""P-16 평가 실행기 시험: 지표 계산(결정 020 ⑤ ~ ⑧ · ⑩) · 출력 schema(AC-18) · ADV-09(정답 자료 파일 열기 감시 · 정적 검사).

지표 시험은 정답을 손으로 정한 기록으로 계산 결과를 비교한다(점검표 C7). 평가 카드 원본은 읽지 않는다.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "eval_watch"))
import eval_metrics  # noqa: E402
import forbidden_open  # noqa: E402

from test_api import api, db, reset_ops, start, to_instruct  # noqa: E402, F401 — fixture(api · db)는 이름으로 쓴다

MANUAL = eval_metrics.Manual(json.loads((REPO / "data/manuals/manual_sections.json").read_text(encoding="utf-8")))


def rec(**kw):
    base = {
        "card_id": "X",
        "group": "CL",
        "hidden": {"type": "manual_cause", "code": "CL-2"},
        "similar_top3_causes": ["CL-1", "CL-1", "CL-2"],
        "initial_probabilities": {"CL-1": 40, "CL-2": 30, "CL-3": 20, "CL-4": 10},
        "check_minutes_until_confirm": [3, 4, 10],
        "citations": [("4.3", "report"), ("4.4", "report"), ("4.5", "unresolved")],
        "performed_sections": ["1.2", "4.2", "4.3", "4.6", "4.4", "4.4.2", "6.1"],
        "remaining_candidate_sections": ["4.5"],
        "candidate_names": ["냉각 필터 막힘", "냉각 팬 열화", "냉각수 부족", "냉각 설정 오류"],
        "report_cause": "냉각 팬 열화",
        "violations": 0,
        "calls": 1,
    }
    return {**base, **kw}


def by_id(summary):
    return {m["id"]: m for m in summary["metrics"]}


def test_metrics_schema_targets_and_unmet_reported():
    """AC-18-C1 · C2: 지표 6개 · 이름 · 목표(80 · 80 · 20 · 95 · 0 · 0) · 값 · 분자 · 분모 · 달성 여부, 미달도 결과로."""
    s = eval_metrics.compute([rec(), rec(similar_top3_causes=["CL-1"] * 3)], MANUAL)
    m = by_id(s)
    assert list(m) == [
        "similar_top3",
        "cause_top3",
        "minutes_reduction",
        "section_match",
        "fabricated_causes",
        "grade_violations",
    ]
    assert [m[k]["target"] for k in m] == [80.0, 80.0, 20.0, 95.0, 0, 0]
    assert m["similar_top3"]["value"] == 50.0 and m["similar_top3"]["met"] is False  # 미달도 값으로 보고
    assert all({"name", "value", "unit", "numerator", "denominator", "met"} <= set(x) for x in m.values())


def test_top3_and_denominator_only_manual_cause_cards():
    """결정 020 ⑤: ① · ②는 정답 원인 카드만 분모, 정답 없는 카드(none · out_of_graph)는 건수만. 실패 카드는 분모에 넣고 맞지 않음."""
    rs = [
        rec(),  # ① 상위 3에 CL-2 있음 · ② 상위 3(CL-1 · CL-2 · CL-3)에 있음
        rec(initial_probabilities={"CL-1": 50, "CL-3": 25, "CL-4": 15, "CL-2": 10}),  # ② CL-2가 4위 → 없음
        rec(hidden={"type": "none"}, similar_top3_causes=[]),
        rec(hidden={"type": "out_of_graph"}),
        rec(failed="x", similar_top3_causes=["CL-2"]),  # 실패 카드는 값이 있어도 맞지 않음
    ]
    s = eval_metrics.compute(rs, MANUAL)
    m = by_id(s)
    assert (m["similar_top3"]["numerator"], m["similar_top3"]["denominator"]) == (2, 3)
    assert (m["cause_top3"]["numerator"], m["cause_top3"]["denominator"]) == (1, 3)
    assert s["ungraded_cards"] == {"none": 1, "out_of_graph": 1} and s["failed_cards"] == 1
    assert eval_metrics.top3({"A": 10, "B": 10, "C": 5, "D": 5}) == ["A", "B", "C"]  # 같으면 코드 순


def test_minutes_reduction_against_manual_order():
    """결정 020 ⑥: 기준 = 매뉴얼 절 순서대로 정답 결정 점검까지(CL-2 → 4.3 3분 + 4.4 10분 = 13), Agent = 확정까지 지시된 점검 분."""
    assert MANUAL.baseline_minutes("CL", "CL-2") == 13 and MANUAL.baseline_minutes("CL", "CL-4") == 3 + 10 + 5 + 4
    s = eval_metrics.compute(
        [
            rec(check_minutes_until_confirm=[10]),
            rec(hidden={"type": "manual_cause", "code": "CL-4"}, check_minutes_until_confirm=[3, 4]),
        ],
        MANUAL,
    )
    m = by_id(s)["minutes_reduction"]
    assert (m["numerator"], m["denominator"]) == (17, 35) and m["value"] == round(100 * (1 - 17 / 35), 1)


def test_failed_card_counts_no_reduction():
    """실패 카드는 단축 0(Agent 분 = 기준 분) — 틀리면 실패: 실패 카드의 0분이 단축률을 올림."""
    ok_only = by_id(eval_metrics.compute([rec(check_minutes_until_confirm=[13])], MANUAL))["minutes_reduction"]
    with_fail = by_id(
        eval_metrics.compute(
            [rec(check_minutes_until_confirm=[13]), rec(failed="x", check_minutes_until_confirm=[])], MANUAL
        )
    )["minutes_reduction"]
    assert (
        ok_only["value"] == 0.0
        and with_fail["value"] == 0.0
        and (with_fail["numerator"], with_fail["denominator"]) == (26, 26)
    )


def test_section_match_rules_and_parser():
    """결정 020 ⑦ ⑩ ⑫: 절 = 실제 단계(지시 · 수행 · 호출)이고 매뉴얼에 있음, 미확인 행은 실제 단계 또는 끝에 후보로 남은 원인의 점검 절."""
    s = eval_metrics.compute(
        [
            rec(
                citations=[
                    ("4.3", "report"),  # 수행 → 일치
                    ("4.5", "report"),  # 수행 안 함 → 불일치
                    ("4.5", "unresolved"),  # 후보로 남은 점검 → 일치
                    ("4.6", "unresolved"),  # 후보에 없고 실제 단계도 아님 → 불일치
                    ("4.3", "unresolved"),  # 미확인 행이어도 실제 단계면 일치(결정 020 ⑫)
                    ("9.9", "instruction"),  # 매뉴얼에 없음 → 불일치
                ],
                performed_sections=["4.3", "9.9"],
                remaining_candidate_sections=["4.5"],
            )
        ],
        MANUAL,
    )
    m = by_id(s)["section_match"]
    assert (m["numerator"], m["denominator"]) == (3, 6)
    text = "흡기 필터 육안 점검(4.3, 이상) → 교체(4.3.2, A) · 재가동(6.1, B, 확인 완료) 후 온도 차 9.1 K 경계 이내 · 4.4절 확인"
    assert eval_metrics.cited_sections(text) == ["4.3", "4.3.2", "6.1", "4.4"]  # 측정값 9.1 K는 세지 않음


def test_outcome_labels_match_product():
    """결정 020 ⑪: 허용하는 결과 표시는 제품의 확정 원인 칸 결과 표시(flow.OUTCOME_CAUSE)와 같다 — 문구가 바뀌면 실패."""
    from c1a15.api import flow

    assert eval_metrics.OUTCOME_LABELS == set(flow.OUTCOME_CAUSE.values())


def test_fabricated_and_violation_counts():
    """결정 020 ⑧: ⑤ = 후보 이름 · 보고서 원인 중 그 증상군 매뉴얼 원인 · '원인 미상' 밖, ⑥ = 승인 없는 C · D 지시 수 합."""
    assert MANUAL.causes["CL"] == {
        "CL-1": "냉각 필터 막힘",
        "CL-2": "냉각 팬 열화",
        "CL-3": "냉각수 부족",
        "CL-4": "냉각 설정 오류",
    }
    s = eval_metrics.compute(
        [
            rec(report_cause="원인 미상"),
            rec(report_cause="이상 없음 · 감시"),  # 결정 020 ⑪: 정해 둔 결과 표시는 날조 아님
            rec(candidate_names=["냉각 필터 막힘", "필터 과열"], report_cause="냉각기 고장", violations=1),
        ],
        MANUAL,
    )
    m = by_id(s)
    assert m["fabricated_causes"]["value"] == 2 and m["fabricated_causes"]["met"] is False
    assert m["grade_violations"]["value"] == 1 and m["grade_violations"]["met"] is False
    assert by_id(eval_metrics.compute([rec()], MANUAL))["grade_violations"]["met"] is True


def test_adv09_static_check_catches_defect_copy(tmp_path):
    """ADV-09(정적): 진단 처리 결함 사본이 평가 카드 경로를 가리키면 scripts/forbidden_paths_check.py가 실패한다."""
    bad = tmp_path / "server" / "src" / "c1a15" / "api"  # 진단 처리 자리(ES-18: 앱 retrieval/ 지움)
    bad.mkdir(parents=True)
    (bad / "sessions.py").write_text('CARDS = "eval/cards/cards.jsonl"\n', encoding="utf-8")

    def run(root):
        return subprocess.run(
            [sys.executable, str(REPO / "scripts/forbidden_paths_check.py"), "--root", str(root)],
            capture_output=True,
            text=True,
        )

    r = run(tmp_path)
    assert r.returncode == 1 and "FORBIDDEN server/src/c1a15/api/sessions.py" in r.stdout
    assert run(REPO).returncode == 0  # 지금 제품 코드는 0건


HITS: list[str] = []
WATCH = {"on": False}
sys.addaudithook(forbidden_open.make_hook(lambda p: HITS.append(p) if WATCH["on"] else None))


@pytest.mark.db
def test_adv09_file_watch_catches_defect_copy(api, db, tmp_path, monkeypatch):
    """ADV-09(실행 감시): 진단 처리(세션 생성 · start Run · 판정)를 파일 열기 감시로 감싼다. 지금 코드 = 0건,
    세션 시작이 가짜 평가 카드(tmp_path 사본)를 여는 결함 사본 = 감시가 잡음(fail closed). 실제 카드 파일은 쓰지 않는다.
    상위 10은 Pack 리트리버(ES-15) — 결함 사본 자리 = 앱이 start Run을 부르는 곳(ES-16)."""
    from c1a15.deploy import runs

    fake = tmp_path / "eval" / "cards" / "cards.jsonl"
    fake.parent.mkdir(parents=True)
    fake.write_text('{"card_id": "FAKE"}\n', encoding="utf-8")
    reset_ops(db)
    HITS.clear()
    WATCH["on"] = True
    try:
        tech, s = start(api, db)
        to_instruct(tech, s)
        assert HITS == []  # 지금 진단 처리는 정답 자료를 열지 않음
        real = runs.start

        def leaky(*a, **kw):  # 결함 사본: 세션 시작이 평가 카드 사본을 연다
            fake.read_text(encoding="utf-8")
            return real(*a, **kw)

        monkeypatch.setattr(runs, "start", leaky)
        start(api, db, equipment="M-03")
    finally:
        WATCH["on"] = False
    assert HITS and all("eval/cards/cards.jsonl" in p for p in HITS)


def test_ch27_not_registered_cards_are_filtered_before_metrics():
    """EV-001 CH-27(GAP-20): ML 길에서 알람이 안 된 카드는 계산 전에 걸러 냄 — 지표 · 요약 수는 등록 카드만, cards는 전체.

    실행 실패(failed)는 지금 규칙(FLOW-12) 그대로 분모에 남는다(A14-N5: registered = cards − not_registered).
    """
    base = [rec(), rec(similar_top3_causes=["CL-2"] * 3), rec(failed="ApiError x", similar_top3_causes=[])]
    skip = {
        "card_id": "Y",
        "group": "CL",
        "hidden": {"type": "manual_cause", "code": "CL-1"},
        "registered": False,
        "reason": "below_threshold",
    }
    plain = eval_metrics.compute(base, MANUAL)
    s = eval_metrics.compute(base[:1] + [skip] + base[1:], MANUAL)
    assert s["metrics"] == plain["metrics"]  # 걸러 낸 카드는 어느 지표에도 들어가지 않음(KeyError 0)
    assert (s["cards"], s["registered"], s["not_registered"]) == (4, 3, 1)
    assert s["graded_cards"] == plain["graded_cards"] == 3 and s["failed_cards"] == 1
    assert (plain["cards"], plain["registered"], plain["not_registered"]) == (3, 3, 0)  # 경계 길: 칸 둘만 더해짐


def test_ch27_eval_run_records_not_registered_without_session(tmp_path, monkeypatch):
    """EV-001 CH-27: 등록 응답에 alarm_id가 없으면(오류 아님) 세션을 열지 않고 registered false로 남김 — ML 길 평가 실행은 NX(S3-Q4)라 단위 시험만."""
    import eval_run

    cards = [
        {"card_id": "C1", "hidden_cause": {"type": "manual_cause", "code": "CL-1"}},
        {"card_id": "C2", "hidden_cause": {"type": "manual_cause", "code": "CL-2"}},
    ]
    inputs = [{"card_id": "C1", "symptom_group": {"code": "CL"}}, {"card_id": "C2", "symptom_group": {"code": "CL"}}]
    (tmp_path / "cards.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cards), encoding="utf-8")
    (tmp_path / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    monkeypatch.setattr(eval_run, "CARDS", tmp_path / "cards.jsonl")
    monkeypatch.setattr(eval_run, "INPUTS", tmp_path / "inputs.json")
    ml = {"model_release": "mr-x", "risk": 0.1, "threshold": 0.5}
    regs = {
        "C1": {"ref": "C1", "alarm_id": None, "reason": "below_threshold", "ml": ml},
        "C2": {"ref": "C2", "alarm_id": 7, "assigned_to": "tech.kim", "symptom_group": "CL"},
    }
    monkeypatch.setattr(eval_run, "register", lambda inp: {inp[0]["card_id"]: regs[inp[0]["card_id"]]})
    opened = []

    class FakeRun:
        def __init__(self, *_):
            pass

        def card(self, card, inp, reg, manual):
            opened.append(card["card_id"])
            return rec(card_id=card["card_id"], hidden=card["hidden_cause"], outcome="원인 확정")

    monkeypatch.setattr(eval_run, "Run", FakeRun)
    out = tmp_path / "report.json"
    assert eval_run.main(["--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert opened == ["C2"]  # 알람 안 된 카드는 세션 0
    c1 = next(r for r in report["records"] if r["card_id"] == "C1")
    assert c1 == {
        "card_id": "C1",
        "group": "CL",
        "hidden": cards[0]["hidden_cause"],
        "registered": False,
        "reason": "below_threshold",
        "ml": ml,
    }
    assert (report["cards"], report["registered"], report["not_registered"], report["graded_cards"]) == (2, 1, 1, 1)


def test_ch37_eval_card_lineage_has_three_fields_only():
    """EV-001 CH-37 · S3-Q9(c1a15-eval 003): 카드 출처 = {source_release, row_id(int UDI), split} 세 칸만 — 다른 카드 파일 칸 0."""
    import eval_run

    inp = {
        "card_id": "EC-V01",
        "equipment_id": "M-15",
        "alarm": {"sensor": "토크"},
        "sensor_snapshot": {"quality_grade": "M", "torque_nm": 61.7},
        "recent_12": [{"at": "t", "torque_nm": 61.0}],
    }
    body = eval_run.alarm_input(inp, {"EC-V01": "7286"})
    assert body["data_lineage"] == {"source_release": eval_run.source_release(), "row_id": 7286, "split": "val"}
    assert eval_run.source_release().startswith("ai4i-2020@sha256:")
    assert set(body) == {"ref", "equipment_id", "sensor_snapshot", "trend", "data_lineage"}
