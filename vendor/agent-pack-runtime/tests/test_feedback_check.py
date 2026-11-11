"""V-CHECK (EV-004 FB-1, CHG-F01 · F02 · D-1): the feedback record rules without a database, and the feedback id. The run's own
state (exists, succeeded, the node allows the action) is the Deployment Server's check, not tested here."""
import re

import pytest

from tracewriter import ids
from tracewriter.feedback import FEEDBACK_CONTRACT, LABEL_LIST_VERSION, LABELS, OK_LABELS, check_feedback

OK = {"feedback_id": "fb_01JC0000000000000000000001", "run_id": "run_01JC0000000000000000000000", "at": "2026-10-21T09:12:03.120Z",
      "action": "edit", "actor": "owner-01", "reason": "원인 1순위가 HDF가 맞음", "retention_class": "standard", "label": "LLM 출력 틀림",
      "review_node": "review", "node": "draft", "edited_value": {"answer.cause_candidates.0.type": "HDF"}, "source": "deploy_api"}


def problems(**change):
    rec = {**OK, **change}
    return check_feedback({k: v for k, v in rec.items() if v is not ...})


def test_a_right_record_passes_and_the_constants():
    assert check_feedback(OK) == []
    assert FEEDBACK_CONTRACT == 1 and LABEL_LIST_VERSION == "labels-v1" and OK_LABELS == ("정상 동작(escalation 맞음)",)
    # the labels-v1 copy, in the LLMOps order (llmops/autodiag.py:401) — llmops and deploy tests compare their own lists (RX-F4)
    assert LABELS == ("검색 못 함", "근거 부족", "규칙 실패", "LLM 출력 틀림", "정상 동작(escalation 맞음)", "Pack 밖 원인")


def test_optional_fields_may_be_left_out_or_none():
    assert check_feedback({k: v for k, v in OK.items() if k not in ("review_node", "node", "source")}) == []
    assert problems(review_node=None, node=None) == []   # no_issue run: no review node (034-02 (1))


@pytest.mark.parametrize("field", ["feedback_id", "run_id", "at", "action", "actor", "reason", "retention_class"])
def test_required_fields(field):
    assert (f"$.{field}", "필요한 칸입니다") in problems(**{field: ...})


@pytest.mark.parametrize("field, value, needle", [
    ("feedback_id", "fb_1", "피드백 id(fb_ + ULID) 모양이 아닙니다"),
    ("feedback_id", "fb_01JC000000000000000000000I", "피드백 id"),          # I is not Crockford
    ("run_id", "run_local", "실행 id(run_ + ULID) 모양이 아닙니다"),
    ("at", "2026-10-21 09:12:03", "시각(ISO, Z로 끝남) 모양이 아닙니다"),
    ("at", "2026-10-21T09:12:03+09:00", "시각"),
    ("source", "llmops", "source는 deploy_api입니다"),
    ("retention_class", "provider_raw", "보존 등급은 standard 또는 dev입니다"),
    ("review_node", "Review", "node id 모양이 아닙니다"),
    ("node", "a" * 65, "node id 모양이 아닙니다"),
    ("action", "accept", "approve · edit · escalate · reject 중 하나입니다"),
])
def test_shapes(field, value, needle):
    assert any(p == f"$.{field}" and needle in m for p, m in problems(**{field: value})), problems(**{field: value})


@pytest.mark.parametrize("action, label, ok", [
    ("approve", None, True), ("approve", "정상 동작(escalation 맞음)", True), ("approve", "근거 부족", False),
    ("escalate", "근거 부족", True), ("escalate", None, False), ("escalate", "정상 동작(escalation 맞음)", False),
    ("reject", "Pack 밖 원인", True), ("reject", "정상", False),
])
def test_action_and_label(action, label, ok):
    rec = {**OK, "action": action, "label": label}
    if action != "edit":
        rec.pop("edited_value")
    got = check_feedback(rec)
    assert (got == []) is ok, got
    if not ok:
        assert got[0][0] == "$.label"


@pytest.mark.parametrize("actor, needle", [
    ("Kim Minsu", "별명 ID"), ("kim@example.com", "별명 ID"), ("010-1234-5678", "별명 ID"), ("12345", "별명 ID"), ("k", "별명 ID"),
    ("a" * 33, "별명 ID"), ("sk-abcdefgh1234", "키 모양 글은 별명으로 쓸 수 없습니다"),
])
def test_actor_is_an_alias_and_not_key_shaped(actor, needle):
    assert any(p == "$.actor" and needle in m for p, m in problems(actor=actor))


@pytest.mark.parametrize("reason, ok", [(" ", False), ("", False), ("x", True), ("가" * 500, True), ("가" * 501, False), ("  가  ", True), (3, False)])
def test_reason_length(reason, ok):
    assert (problems(reason=reason) == []) is ok


def test_edited_value_rules():
    assert ("$.edited_value", "edit에는 고친 값 {경로: 값} 1 ~ 50개가 필요합니다") in problems(edited_value=...)
    assert ("$.edited_value", "edit에는 고친 값 {경로: 값} 1 ~ 50개가 필요합니다") in problems(edited_value={})
    assert ("$.edited_value", "edit에는 고친 값 {경로: 값} 1 ~ 50개가 필요합니다") in problems(edited_value={f"answer.f{i}": i for i in range(51)})
    assert problems(edited_value={f"answer.f{i}": i for i in range(50)}) == []
    assert ("$.edited_value", "edit 밖의 동작에는 고친 값을 두지 않습니다") in problems(action="escalate")
    assert problems(edited_value={"answer.items.0.id": 1, "answer": {"a": 1}, "_x.y_2": None}) == []


@pytest.mark.parametrize("path, needle", [
    ("answer..type", "칸 경로 모양이 아닙니다"), ("0.answer", "칸 경로 모양이 아닙니다"), ("answer.ty pe", "칸 경로 모양이 아닙니다"),
    ("a" * 201, "칸 경로 모양이 아닙니다"),
    ("ghp_abcdefghijklmnopqrstuvwxyz", "키 모양 글은 칸 경로로 쓸 수 없습니다"),        # D-1
    ("answer.AKIAABCDEFGHIJKLMNOP", "키 모양 글은 칸 경로로 쓸 수 없습니다"),
])
def test_edit_paths(path, needle):
    got = problems(edited_value={path: 1})
    shown = {"ghp_abcdefghijklmnopqrstuvwxyz": "[가림]", "answer.AKIAABCDEFGHIJKLMNOP": "answer.[가림]", "a" * 201: "a" * 200}.get(path, path)
    assert got == [(f'$.edited_value["{shown}"]', needle)], got   # the path is shown redacted and cut — never the key itself


def test_a_long_key_shaped_path_is_neither_quoted_nor_long():
    key = "sk-proj-" + "x" * 300
    (where, _), = problems(edited_value={key: 1})
    assert "xxxxxxxx" not in where and len(where) < 40


def test_the_writer_secrets_count_as_key_shaped():
    secret = "Topsecretvalue123"
    assert check_feedback({**OK, "edited_value": {f"answer.{secret}": 1}}) == []          # without the list: an ordinary path
    got = check_feedback({**OK, "edited_value": {f"answer.{secret}": 1}, "actor": "topsecretvalue123"}, secrets=[secret, "topsecretvalue123"])
    assert ("$.actor", "키 모양 글은 별명으로 쓸 수 없습니다") in got and ('$.edited_value["answer.[가림]"]', "키 모양 글은 칸 경로로 쓸 수 없습니다") in got
    assert secret not in str(got)


def test_unknown_fields_and_order():
    got = check_feedback({"run_id": "x", "role": "정비", "action": "approve", "extra": 1, "reason": " "})
    assert got == [("$.feedback_id", "필요한 칸입니다"), ("$.run_id", "실행 id(run_ + ULID) 모양이 아닙니다"), ("$.actor", "필요한 칸입니다"),
                   ("$.reason", "이유는 1 ~ 500자 글입니다"), ("$.retention_class", "필요한 칸입니다"), ("$.at", "필요한 칸입니다"),
                   ("$.role", "모르는 칸입니다"), ("$.extra", "모르는 칸입니다")]   # DDL column order, unknown fields last (role is null in v1)
    assert check_feedback("x") == [("$", "피드백 기록은 객체입니다")]


def test_no_value_is_written_into_a_message():
    msgs = " ".join(m for _, m in problems(reason="sk-proj-secretsecretsecret " * 30, actor="Kim Minsu"))
    assert "secret" not in msgs and "Kim" not in msgs


# CHG-F02
def test_new_feedback_id_and_run_id_keep_their_shapes():
    f, r = ids.new_feedback_id(), ids.new_run_id()
    assert re.match(r"^fb_[0-9A-HJKMNP-TV-Z]{26}$", f) and re.match(r"^run_[0-9A-HJKMNP-TV-Z]{26}$", r)
    assert check_feedback({**OK, "feedback_id": f}) == []
    a, b = ids.new_feedback_id(now=1_700_000_000.0), ids.new_feedback_id(now=1_700_000_001.0)
    assert a[3:13] < b[3:13]   # time first: later ids sort later
    assert ids.new_run_id(now=1_700_000_000.0)[4:14] == a[3:13]   # the same time part as a run id of that moment


def test_run_ids_are_made_as_before(monkeypatch):
    monkeypatch.setattr(ids.os, "urandom", lambda n: bytes(range(n)))
    assert ids.new_run_id(now=1_760_000_000.123) == "run_01K742SG3V000G40R40M30E209"   # what the 0.1.2 code (301a120) gives
    assert ids.new_feedback_id(now=1_760_000_000.123) == "fb_01K742SG3V000G40R40M30E209"
