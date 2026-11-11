"""V-DECIDE-IR (EV-001 CHG-101 · CHG-105): decide@1 loads and is checked (spec 11, 7.1); chat-model decide is refused (H-1 A);
question texts are the only editable parts of the questions (spec 11 ✎)."""
import re
import shutil
from pathlib import Path

import pytest

import packrt
from packrt.ir import load_pack, semantic
from packrt.ir.patch import apply_patch, editable_params

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "decide_min"


def pack(tmp_path, edit=None, models=None):
    """A copy of decide_min that loads on this runtime (its min_runtime_version is the release's own), optionally edited."""
    work = tmp_path / "p"
    shutil.copytree(FIXTURE, work)
    text = (work / "pack.yaml").read_text(encoding="utf-8")
    text = text.replace("min_runtime_version: 0.10.0", f"min_runtime_version: {packrt.__version__}")
    if edit:
        text = edit(text)
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    if models is not None:
        (work / "models.yaml").write_text(models, encoding="utf-8")
    return work


def messages(work):
    return [e["message"] for e in load_pack(work).errors]


def test_decide_is_a_supported_kind_and_the_fixture_loads(tmp_path):
    assert "decide@1" in semantic.SUPPORTED
    r = load_pack(pack(tmp_path))
    assert r.ok, r.errors
    assert r.revision.startswith("sha256:")


def test_a_chat_model_decide_is_refused(tmp_path):
    work = pack(tmp_path, models="providers:\n  jev: {env: JEV_API_KEY, base_url_env: JEV_BASE_URL, models: {chat: [jev-1.13.0]}}\n")
    assert any("chat 모델로 decide를 아직 하지 않습니다" in m for m in messages(work))


def test_a_model_not_in_the_decision_list_is_refused(tmp_path):
    work = pack(tmp_path, edit=lambda t: t.replace("model: jev-1.13.0", "model: jev-9"))
    assert any("decision 목록에 'jev-9'가 없습니다" in m for m in messages(work))


@pytest.mark.parametrize("edit, needle", [
    (lambda t: t.replace("writes: [triage]", "writes: [triage, note]"), "writes가 정확히 1개"),
    (lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, nope: 1}"), "fallback의 'nope'가 questions에 없습니다"),
    (lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: yes_please}"), "질문 종류(noul)에 맞지 않습니다"),
    (lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, category: travel}"), "질문 종류(choice)에 맞지 않습니다"),
    (lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, severity: 4}"), "질문 종류(score)에 맞지 않습니다"),
    (lambda t: t.replace("min_confidence: {urgent: 0.7}", "min_confidence: {urgent: 0.7, nope: 0.5}"), "min_confidence의 'nope'가 questions에 없습니다"),
    (lambda t: t.replace("min_confidence: {urgent: 0.7}", "min_confidence: 0.6"), "min_confidence가 걸린 질문 'category'에 fallback이 없습니다"),
    (lambda t: t.replace('state: {text: "{state.ticket.text}", channel: "{state.ticket.channel}"}', "state: [ticket, note]"), "reads에 없는 'note'"),
    (lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, severity: true}"), "질문 종류(score)에 맞지 않습니다"),
    (lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, severity: 0}"), "질문 종류(score)에 맞지 않습니다"),
])
def test_semantic_checks(tmp_path, edit, needle):
    msgs = messages(pack(tmp_path, edit=edit))
    assert any(needle in m for m in msgs), msgs


@pytest.mark.parametrize("edit", [
    lambda t: t.replace("min_confidence: {urgent: 0.7}", "min_confidence: {urgent: 1.5}"),
    lambda t: t.replace("min_confidence: {urgent: 0.7}", "min_confidence: true"),
    lambda t: t.replace("type: score, criteria: [\"낮음\", \"보통\", \"높음\"]", "type: score, criteria: [\"하나\"]"),
    lambda t: t.replace("type: noul,", "type: maybe,"),
    lambda t: t.replace("          fallback: {urgent: true}\n", "          fallback: {urgent: true}\n          extra_key: 1\n"),
])
def test_schema_refuses_bad_shapes(tmp_path, edit):
    r = load_pack(pack(tmp_path, edit=edit))
    assert not r.ok and any(e["stage"] == "schema" for e in r.errors), r.errors


def test_a_state_key_list_read_by_the_node_is_accepted(tmp_path):
    work = pack(tmp_path, edit=lambda t: t.replace('state: {text: "{state.ticket.text}", channel: "{state.ticket.channel}"}', "state: [ticket]"))
    assert load_pack(work).ok


def test_the_fallback_flag_and_valid_fallbacks_are_accepted(tmp_path):
    work = pack(tmp_path, edit=lambda t: t.replace("fallback: {urgent: true}", "fallback: {urgent: true, category: other, severity: 3}"))
    assert load_pack(work).ok


def node(doc, nid="assess"):
    return next(n for n in doc["graphs"]["triage"]["nodes"] if n["id"] == nid)


def test_editable_params_of_decide(tmp_path):
    doc = load_pack(pack(tmp_path)).doc
    assert editable_params(doc, node(doc)) == {"provider", "model", "min_confidence", "fallback"}


def op(param, value=None, name="set"):
    out = {"op": name, "graph": "triage", "node": "assess", "param": param}
    if name == "set":
        out["value"] = value
    return out


@pytest.mark.parametrize("ops, check", [
    ([op("questions.urgent.instructions", "고객이 지금 멈춰 있는가")], lambda p: p["questions"]["urgent"]["instructions"] == "고객이 지금 멈춰 있는가"),
    ([op("questions.urgent.instructions", name="unset")], lambda p: "instructions" not in p["questions"]["urgent"]),
    ([op("questions.urgent.criteria.true", "서비스 중단")], lambda p: p["questions"]["urgent"]["criteria"]["true"] == "서비스 중단"),
    ([op("questions.category.criteria.other", "그 밖")], lambda p: p["questions"]["category"]["criteria"]["other"] == "그 밖"),
    ([op("questions.category.criteria.billing", None)], lambda p: p["questions"]["category"]["criteria"]["billing"] is None),
    ([op("questions.severity.criteria[2]", "매우 높음")], lambda p: p["questions"]["severity"]["criteria"] == ["낮음", "보통", "매우 높음"]),
    ([op("min_confidence", {"urgent": 0.8})], lambda p: p["min_confidence"] == {"urgent": 0.8}),
    ([op("fallback", {"urgent": False})], lambda p: p["fallback"] == {"urgent": False}),
])
def test_question_texts_and_editable_params_can_change(tmp_path, ops, check):
    before = node(load_pack(pack(tmp_path / "a")).doc)["params"]
    assert not check(before)   # the edit really changes something
    res = apply_patch(pack(tmp_path / "b"), ops, out_dir=tmp_path / "out")
    assert res.ok, res.errors
    after = node(load_pack(tmp_path / "out").doc)["params"]
    assert check(after)
    q = after["questions"]
    assert set(q) == {"urgent", "category", "severity"} and [q[k]["type"] for k in ("urgent", "category", "severity")] == ["noul", "choice", "score"]


@pytest.mark.parametrize("ops, needle", [
    ([op("questions", {"urgent": {"type": "noul", "criteria": {"true": "a", "false": "b"}}})], "수정할 수 없는 항목"),
    ([op("questions.nope.instructions", "x")], "질문 'nope'가 없습니다"),
    ([op("questions.category.criteria.travel", "여행")], "선택지 'travel'가 없습니다"),
    ([op("questions.severity.criteria[3]", "넷째")], "3번 단계가 없습니다"),
    ([op("questions.urgent.criteria.true", name="unset")], "지울 수 없습니다"),
    ([op("questions.urgent.criteria.true", 3)], "글이어야 합니다"),
    ([op("questions.urgent.type", "choice")], "수정할 수 없는 항목"),
    ([op("state", ["ticket"])], "수정할 수 없는 항목"),
])
def test_question_structure_cannot_change(tmp_path, ops, needle):
    res = apply_patch(pack(tmp_path), ops, out_dir=tmp_path / "out")
    assert not res.ok and any(needle in e["message"] for e in res.errors), res.errors


def test_a_pack_without_decide_is_unchanged_by_the_new_checks():
    r = load_pack(Path(__file__).resolve().parent / "fixtures" / "task10_min")
    assert r.ok and r.revision == "sha256:1f573d6fbf764b0f12d35702fcf289b8836856248a220c9d17a2ee2f22f4f419"
    assert re.fullmatch(r"\d+\.\d+\.\d+", packrt.__version__)
