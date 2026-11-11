"""D-619 사전 점검 후보 셈(순수 함수 · 약속 handoff/d619/precheck-contract.md 3절 · 세는 곳 = 앱 서버).
예시 줄(표시 D-619)과 표에 적은 시연 줄만 셈 · 진행 중은 분모 밖 · 승인 예외는 배포가 바뀐 뒤에만 이어짐."""

from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


def line(
    rid, eq, days_ago, *, did=(), bad=(), good=(), group="CL", topic=None, demo="D-619", source="generated", ex=None
):
    rec = {
        "steps": [
            *({"kind": "check", "section": s, "result": "이상"} for s in bad),
            *({"kind": "check", "section": s, "result": "정상"} for s in good),
            *({"kind": "action", "section": s, "name": f"이름 {s}"} for s in did),
        ],
        "outcome": {"type": "원인 미상·숙련자 인계"},  # 예시 줄 원인 칸 비움(관문 확률 영향 0 · graph-values 01:13)
        "demo": demo,
    }
    if topic:
        rec["exception_record"] = {"text": topic}
    if ex:
        rec["exception_record"] = ex
    return {
        "record_id": rid,
        "equipment_id": eq,
        "symptom_group": group,
        "occurred_at": NOW - timedelta(days=days_ago),
        "source": source,
        "record": rec,
    }


def pattern(pid):
    from c1a15.api import precheck

    return next(p for p in precheck.PATTERNS if p.id == pid)


def count(pid, rows, reflected=frozenset()):
    from c1a15.api import precheck

    return precheck.classify(pattern(pid), rows, NOW, reflected)


def a_rows():
    """A: 이어짐 둘 · 이상 없음 둘 · 시연 H-0301(3일 전) · 지금 설비 하나."""
    return [
        line("H-P001", "M-04", 90, did=["4.3.2"]),
        line("H-P002", "M-04", 85, topic="필터 덮개 고정"),
        line("H-P003", "M-05", 80, did=["4.3.2"]),
        line("H-P004", "M-05", 75, topic="필터 덮개 고정"),
        line("H-P005", "M-06", 50, did=["4.3.2"]),
        line("H-P006", "M-08", 40, did=["4.3.2"]),
        line("H-P007", "M-07", 2, did=["4.3.2"]),
        line("H-0301", "M-01", 3, did=["4.3.2"], demo="D-570"),
    ]


APPROVED = line(
    "R-8001",
    "M-01",
    1,
    demo=None,
    source="approved_report",
    ex={"text": "흡기 필터 덮개 볼트가 덜 조여져 있었음", "exception": {"topic": "흡기 필터 덮개 볼트"}},
)


def test_a_before_approval_two_of_four_and_open_rows_stay_out_of_total():
    got = count("A", a_rows())
    assert (len(got["hits"]), len(got["clean"])) == (2, 2)
    assert sorted(r["record_id"] for r in got["open"]) == ["H-0301", "H-P007"]


def test_approved_exception_counts_only_after_the_deployed_graph_changed():
    assert len(count("A", [*a_rows(), APPROVED])["hits"]) == 2
    got = count("A", [*a_rows(), APPROVED], frozenset({"R-8001"}))
    assert (len(got["hits"]), len(got["clean"])) == (3, 2)
    hit = next(r for r in got["hits"] if r["record_id"] == "H-0301")
    assert (hit["follow_id"], hit["follow_days"]) == ("R-8001", 2)
    assert [r["record_id"] for r in got["open"]] == ["H-P007"]


def test_only_marked_lines_count_real_history_is_not_the_total():
    real = line("H-0100", "M-09", 120, did=["4.3.2"], demo=None)
    got = count("A", [*a_rows(), real])
    assert "H-0100" not in {r["record_id"] for k in ("hits", "clean", "open") for r in got[k]}


def test_follow_must_be_on_the_same_equipment_and_inside_the_window():
    rows = [
        line("H-P101", "M-09", 90, did=["2.4.2"], good=["2.3"], group="TW"),
        line("H-P102", "M-10", 88, bad=["2.3"], group="TW"),  # 다른 설비
        line("H-P103", "M-09", 70, bad=["2.3"], group="TW"),  # 14일 밖
        line("H-P104", "M-09", 85, good=["2.3"], group="TW"),  # 그 절 정상은 뒤 사건 아님
    ]
    got = count("B", rows)
    assert (len(got["hits"]), [r["record_id"] for r in got["clean"]]) == (0, ["H-P101"])


def test_follow_is_the_check_section_found_abnormal():
    rows = [
        line("H-P101", "M-09", 90, did=["2.4.2"], good=["2.3"], group="TW"),
        line("H-P102", "M-09", 80, bad=["2.3"], group="TW"),
    ]
    got = count("B", rows)
    assert [(r["record_id"], r["follow_id"], r["follow_days"]) for r in got["hits"]] == [("H-P101", "H-P102", 10)]


def test_exception_without_the_pattern_word_is_not_a_follow():
    other = {
        **APPROVED,
        "record": {**APPROVED["record"], "exception_record": {"text": "팬 소리", "exception": {"topic": "팬"}}},
    }
    assert len(count("A", [*a_rows(), other], frozenset({"R-8001"}))["hits"]) == 2


@pytest.mark.parametrize(
    ("hits", "clean", "show"), [(2, 2, False), (3, 2, True), (3, 4, False), (3, 3, True), (0, 0, False)]
)
def test_show_needs_three_hits_and_at_least_half(hits, clean, show):
    from c1a15.api import precheck

    assert precheck.shows(hits, clean) is show


def test_words_stay_off_the_banned_list():
    from c1a15.api import precheck

    assert not [p for p in precheck.PATTERNS if "연쇄" in p.follow + p.look_at]


def f_rows():
    """D-656 F(컨트롤러 펌웨어 업데이트 7.1 · PW → 같은 설비 7일 안 냉각 설정 확인 4.6 이상):
    다른 설비 넷(이어짐 둘 · 이상 없음 둘) · M-02 업데이트 5일 전 · M-04 업데이트 3일 전
    (둘 다 진행 중 · 분모 밖) — 예시 대본 번호."""
    return [
        line("H-P026", "M-05", 60, did=["7.1"], group="PW"),
        line("H-P027", "M-05", 55, good=["4.3"], bad=["4.6"]),
        line("H-P028", "M-06", 61, did=["7.1"], group="PW"),
        line("H-P029", "M-06", 56, good=["4.3"], bad=["4.6"]),
        line("H-P030", "M-07", 52, did=["7.1"], group="PW"),
        line("H-P031", "M-08", 33, did=["7.1"], group="PW"),
        line("H-P032", "M-02", 5, did=["7.1"], group="PW"),
        line("H-P033", "M-04", 3, did=["7.1"], group="PW"),
    ]


def m02_report(days_ago=0.5, *, bad=("4.6",), good=("4.3",)):
    """M-02 보통 보고서(예외 아님) 승인 이력 — 4.3 정상(헛걸음) → 4.6 이상 → 4.6.2 설정값 변경."""
    return line("R-8101", "M-02", days_ago, bad=bad, good=good, did=["4.6.2"], demo=None, source="approved_report")


def test_f_first_state_two_of_four_and_both_updates_are_open():
    got = count("F", f_rows())
    assert (len(got["hits"]), len(got["clean"])) == (2, 2)
    assert sorted(r["record_id"] for r in got["open"]) == ["H-P032", "H-P033"]


def test_f_a_plain_approved_report_counts_at_once_no_graph_wait():
    """D-656 ②: 보통 보고서(예외 아님) 승인 = 이력 적재 그 순간부터 셈(배포 revision 기다림 0)
    → M-02 셋째 이어짐 · 5건 중 3건."""
    got = count("F", [*f_rows(), m02_report()])
    assert (len(got["hits"]), len(got["clean"])) == (3, 2)
    hit = next(r for r in got["hits"] if r["record_id"] == "H-P032")
    assert (hit["follow_id"], hit["follow_days"]) == ("R-8101", 4)
    assert [r["record_id"] for r in got["open"]] == ["H-P033"]


def test_f_report_without_4_6_abnormal_or_other_machine_is_not_a_follow():
    assert len(count("F", [*f_rows(), m02_report(bad=(), good=("4.3", "4.6"))])["hits"]) == 2
    other = {**m02_report(), "equipment_id": "M-03"}
    assert len(count("F", [*f_rows(), other])["hits"]) == 2


def test_report_check_feeds_check_patterns_but_not_the_exception_word_pattern():
    """승인 보고서의 점검 '이상'은 점검 절 패턴(B · C · F)에만 — 예외 글 패턴 A는 그대로(예외 글 · 그래프 반영 뒤)."""
    rep = line("R-8102", "M-01", 1, bad=["4.3"], demo=None, source="approved_report")
    assert len(count("A", [*a_rows(), rep], frozenset({"R-8102"}))["hits"]) == 2
    tw = [line("H-P101", "M-09", 20, did=["2.4.2"], group="TW")]
    rep_b = line("R-8103", "M-09", 10, bad=["2.3"], group="TW", demo=None, source="approved_report")
    assert [r["follow_id"] for r in count("B", [*tw, rep_b])["hits"]] == ["R-8103"]


def test_d670_card_short_words_f_is_firmware_and_setting_error_others_fall_back():
    """D-670 카드 펼침 한 줄 '3일 전 펌웨어 업데이트(7.1) · 7일 안 설정 오류 5건 중 3건' — 화면용 짧은 말(셈 손 0)."""
    from c1a15.api import precheck

    f = next(p for p in precheck.PATTERNS if p.id == "F")
    a = next(p for p in precheck.PATTERNS if p.id == "A")
    assert precheck.short_words(f, said=True, section_title="컨트롤러 펌웨어 업데이트") == (
        "펌웨어 업데이트",
        "설정 오류",
    )
    assert precheck.short_words(f, said=False, section_title="전기 계통 작업") == ("전기 계통 작업", "설정 오류")
    assert precheck.short_words(a, said=False, section_title="흡기 필터 교체") == (
        "흡기 필터 교체",
        "필터 덮개 고정 문제",
    )
