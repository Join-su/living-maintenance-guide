"""V-FAKEJEV (EV-001 CHG-104): packrt.fake.jev answers in the Jev shape, the same input gives the same answer, probabilities add
up to 1, Pack overrides replace answers, and every answer says it is fake."""
import pytest

from packrt.fake.jev import LIMIT, FakeJevProvider, respond

QUESTIONS = {
    "urgent": {"type": "noul", "criteria": {"true": "멈춤", "false": "기다려도 됨"}},
    "category": {"type": "choice", "criteria": {"billing": "결제", "technical": "기술", "other": None}},
    "severity": {"type": "score", "criteria": ["낮음", "보통", "높음"]},
}
STATE = {"text": "결제가 두 번 됐어요", "channel": "web"}


def test_shape_of_each_question_type():
    out = respond(STATE, QUESTIONS)
    a = out["answers"]
    assert out["model"] == "jev-1.13.0" and out["limits"] == [LIMIT] and set(out["usage"]) == {"input_tokens", "output_tokens"}
    assert a["urgent"]["type"] == "noul" and 0.05 <= a["urgent"]["noul"] <= 0.95 and "confidence" not in a["urgent"]
    c = a["category"]
    assert c["type"] == "choice" and c["choice"] in QUESTIONS["category"]["criteria"] and set(c["probabilities"]) == {"billing", "technical", "other"}
    assert c["confidence"] == max(c["probabilities"].values()) == c["probabilities"][c["choice"]]
    s = a["severity"]
    assert s["type"] == "score" and 1 <= s["score"] <= 3 and set(s["probabilities"]) == {"1", "2", "3"} and s["legend"] == ["낮음", "보통", "높음"]
    assert s["confidence"] == s["probabilities"][str(s["score"])]
    for p in (c["probabilities"], s["probabilities"]):
        assert abs(sum(p.values()) - 1) < 1e-9


def test_same_input_same_answer_and_other_input_other_answer():
    assert respond(STATE, QUESTIONS) == respond(dict(STATE), dict(QUESTIONS))
    seen = {respond({"text": f"문의 {i}", "channel": "web"}, QUESTIONS)["answers"]["urgent"]["noul"] for i in range(20)}
    assert len(seen) > 10   # the state changes the answer


def test_overrides_replace_answers():
    forced = {"type": "noul", "noul": 0.99}
    out = respond(STATE, QUESTIONS, overrides={"urgent": forced})
    assert out["answers"]["urgent"] == forced and out["answers"]["category"] == respond(STATE, QUESTIONS)["answers"]["category"]


def test_unknown_question_type_is_refused():
    with pytest.raises(ValueError, match="모르는 질문 종류"):
        respond(STATE, {"x": {"type": "maybe", "criteria": {}}})


def test_provider_object_decides_only():
    p = FakeJevProvider()
    raw, usage = p.decide("jev", "jev-1.13.0", STATE, QUESTIONS)
    assert raw == respond(STATE, QUESTIONS) and usage == raw["usage"] and p.last_response() == raw and p.calls[0]["state"] == STATE
    with pytest.raises(RuntimeError, match="decide만"):
        p.chat("openai", "m", [])


def test_probabilities_stay_valid_over_many_inputs():
    for i in range(300):
        q = {"c": {"type": "choice", "criteria": {f"k{j}": None for j in range(1 + i % 12)}}, "s": {"type": "score", "criteria": [str(j) for j in range(2 + i % 9)]}}
        a = respond({"i": i}, q)["answers"]
        for x in (a["c"], a["s"]):
            assert all(p >= 0 for p in x["probabilities"].values()) and abs(sum(x["probabilities"].values()) - 1) < 1e-9
