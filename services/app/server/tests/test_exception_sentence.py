"""D-642 C 예외 점검 안내 문장: 안내 차례 한 줄 = 지시 문장(무엇을 · 어떻게 · 판정 기준).
AI 나눔 줄의 선택 칸 instruction(Pack이 만든 문장)을 그대로 싣고 넘김 · 없으면 앱이 세 칸을 이어 씀(지어낸 값 0)."""

from c1a15.api import flow

CK = {"what": "흡기 필터 덮개 볼트", "how": "손으로 돌려 봄", "criteria": "덜 조여져 있으면 이상", "minutes": None}
AC = {"what": "다시 조임", "parts": [], "minutes": None}
SAY = "흡기 필터 덮개 볼트 네 곳을 손으로 돌려 보고, 덜 조여져 있으면 이상으로 적습니다."


def _out(checks, actions):
    return {"exception_split": {"exception": {"topic": "덮개 볼트", "checks": checks, "actions": actions}}}


def test_split_keeps_the_pack_sentence_and_old_shape_stays_without_it():
    got = flow.split_exception(
        _out([{**CK, "instruction": f"  {SAY} "}], [{**AC, "instruction": "볼트를 다시 조입니다."}])
    )
    assert got["checks"] == [{**CK, "instruction": SAY}]
    assert got["actions"] == [{**AC, "instruction": "볼트를 다시 조입니다."}]
    old = flow.split_exception(_out([CK, {**CK, "instruction": "  "}], [AC]))
    assert old["checks"] == [CK, CK] and old["actions"] == [AC]  # 빈 문장 · 없는 문장 = 칸 안 실음(옛 꼴 그대로)


def test_say_is_the_approved_sentence_or_the_three_fields_joined():
    assert flow.say_of({**CK, "instruction": SAY}, "check") == SAY
    assert flow.say_of(CK, "check") == "흡기 필터 덮개 볼트 · 손으로 돌려 봄 · 기준 덜 조여져 있으면 이상"
    assert flow.say_of({"what": "흡기 필터 덮개 볼트", "how": "", "criteria": "덜 조여져 있었음"}, "check") == (
        "흡기 필터 덮개 볼트 · 기준 덜 조여져 있었음"
    )
    assert flow.say_of({"what": "다시 조임", "parts": ["M6 볼트"]}, "action") == "다시 조임 · 부품 M6 볼트"


def test_walk_row_carries_the_sentence_and_now_stays_a_separate_flag():
    eg = {
        "section": "EX-R-1",
        "relation_id": "EX-R-1",
        "checks": [{**CK, "instruction": SAY}, {"what": "흡기 덕트 이음매", "how": "", "criteria": ""}],
        "actions": [AC],
    }
    events = [{"type": "step_opened", "seq": 1, "exception_guide": eg}]
    rows = flow.walk_of(events, {"kind": "instruct", "section": "EX-R-1", "step": "c1"})
    assert [(r["step"], r["what"], r["say"], r["now"]) for r in rows] == [
        ("c1", CK["what"], SAY, True),
        ("c2", "흡기 덕트 이음매", "흡기 덕트 이음매", False),
        ("a1", "다시 조임", "다시 조임", False),
    ]
    assert all("지금" not in r["say"] for r in rows)
