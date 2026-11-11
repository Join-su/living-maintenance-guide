"""V-IR (EV-002 CHG-L01 · L02 · L03, PRES-L01 · L02): llm@1 takes `strict` and `seed`; a node that turns them on is checked against
OpenAI's strict structured-output rules at load (errors name the fields, undocumented keywords are warnings); `strict: false`
alone is the same as no key; a Pack without them loads exactly as with the 7f565ac code (run EV-002 evidence/LS-0-characterization.json)."""
import json
import shutil
from pathlib import Path

import pytest

import packrt
from packrt.ir import load_pack, strict_schema_problems

FIX = Path(__file__).resolve().parent / "fixtures"
# taken with the 7f565ac code before this change (LS-0): no errors, no warnings and these revisions
OFF_REVISIONS = {"task10_min": "sha256:1f573d6fbf764b0f12d35702fcf289b8836856248a220c9d17a2ee2f22f4f419",
                 "decide_min": "sha256:312c38e31be7c7f23f523336b0e37dd30b58f770b7161b79f083a731bd01f297",
                 "llm_min": "sha256:3836c56828fc1794d4f52869dbadb00b46103588f7f72c3f56bdc9fb4a8cac97"}
NODE = "{id: draft, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/draft.md, output: Answer, temperature: 0}"


def pack(tmp_path, params=None, schema=None, models=None, output=None):
    work = tmp_path / "p"
    shutil.copytree(FIX / "llm_min", work)
    text = (work / "pack.yaml").read_text(encoding="utf-8")
    new = NODE
    if output:
        new = new.replace("output: Answer", f"output: {output}")
    if params:
        new = new.replace("temperature: 0}", "temperature: 0, " + params + "}")
    text = text.replace(NODE, new)
    if models:
        text = text.replace("provider: openai", f"provider: {models[0]}").replace("model: gpt-4.1-mini", f"model: {models[1]}")
        (work / "models.yaml").write_text((work / "models.yaml").read_text(encoding="utf-8")
                                          + f"  {models[0]}: {{env: OPENAI_API_KEY, base_url_env: OPENAI_BASE_URL, models: {{chat: [{models[1]}]}}}}\n", encoding="utf-8")
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    if schema is not None:
        (work / "schemas" / "answer.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    return load_pack(work)


def msgs(res, kind="errors"):
    return [p["message"] for p in getattr(res, kind)]


def obj(props, required=None, **kw):
    return {"type": "object", "additionalProperties": False, "required": list(props) if required is None else required, "properties": props, **kw}


@pytest.mark.parametrize("name", sorted(OFF_REVISIONS))
def test_off_pack_load_result_is_unchanged(name):
    res = load_pack(FIX / name)
    assert (res.errors, res.warnings, res.revision) == ([], [], OFF_REVISIONS[name])


def test_strict_and_seed_are_taken(tmp_path):
    res = pack(tmp_path, "strict: true, seed: 7")
    assert res.ok and res.errors == [] and res.warnings == []


def test_seed_alone_is_taken(tmp_path):
    assert pack(tmp_path, "seed: 0").ok


@pytest.mark.parametrize("params", ['strict: "yes"', "seed: 1.5", "seed: true", "strict: 1"])
def test_wrong_types_are_refused_at_stage_1(tmp_path, params):
    res = pack(tmp_path, params)
    assert not res.ok and res.errors[0]["stage"] == "schema"


def test_the_revision_follows_the_pack_text(tmp_path):
    a, b = pack(tmp_path / "a", "strict: true"), pack(tmp_path / "b", "strict: false")
    assert len({a.revision, b.revision, OFF_REVISIONS["llm_min"]}) == 3


def test_strict_with_text_output_is_refused(tmp_path):
    res = pack(tmp_path, "strict: true", output="text")
    assert "node 'draft'는 output이 text라 strict를 쓸 수 없습니다(strict는 schema 출력에만 씁니다)" in msgs(res)


@pytest.mark.parametrize("params", ["strict: true", "seed: 7"])
def test_strict_or_seed_on_another_provider_is_refused(tmp_path, params):
    res = pack(tmp_path, params, models=("anthropic", "claude-x"))
    assert "node 'draft'의 strict · seed는 OpenAI 형식 provider에서만 보냅니다(받은 것: anthropic)" in msgs(res)


@pytest.mark.parametrize("kw", [{"models": ("anthropic", "claude-x")}, {"output": "text"}, {}])
def test_strict_false_alone_is_the_same_as_no_key(tmp_path, kw):
    """No strict check and no provider check: the problems are those of the same Pack without the key."""
    with_key, without = pack(tmp_path / "a", "strict: false", **kw), pack(tmp_path / "b", None, **kw)
    assert (msgs(with_key), msgs(with_key, "warnings")) == (msgs(without), msgs(without, "warnings"))


def test_an_optional_field_is_refused_with_its_name_and_the_hint(tmp_path):
    schema = obj({"a": {"type": "string"}, "b": {"type": "string"}}, required=["a"])
    m = msgs(pack(tmp_path, "strict: true", schema))
    assert m == ["node 'draft'의 output 스키마 Answer이 strict 구조화 출력 조건에 맞지 않습니다: $: required에 없는 칸 b — "
                 '선택 칸은 ["타입","null"]로 쓰고 required에 넣습니다 (schemas/answer.json)']


def test_the_same_schema_without_strict_still_loads(tmp_path):
    schema = obj({"a": {"type": "string"}, "b": {"type": "string"}}, required=["a"])
    assert pack(tmp_path, None, schema).ok


def test_the_task10_grounded_response_names_its_optional_field(tmp_path):
    work = tmp_path / "t"
    shutil.copytree(FIX / "task10_min", work)
    text = (work / "pack.yaml").read_text(encoding="utf-8")
    old = "prompt: prompts/draft.md, output: GroundedResponse}"
    assert old in text
    (work / "pack.yaml").write_text(text.replace(old, "prompt: prompts/draft.md, output: GroundedResponse, strict: true}"), encoding="utf-8")
    m = [x for x in msgs(load_pack(work)) if "strict 구조화 출력 조건" in x]
    assert len(m) == 1 and "required에 없는 칸 escalate" in m[0] and "node 'draft'" in m[0]


@pytest.mark.parametrize("schema, part", [
    ({"type": "object", "required": [], "properties": {}}, "$: additionalProperties false 아님"),
    ({"anyOf": [obj({"a": {"type": "string"}})]}, "$: 뿌리 type이 object가 아닙니다 · $: 뿌리에 anyOf가 있습니다"),
    (obj({"x": {"type": "object", "properties": {"y": {"type": "string"}}, "required": ["y"]}}), "$.x: additionalProperties false 아님"),
    (obj({"xs": {"type": "array", "items": obj({"y": {"type": "string"}}, required=[])}}), "$.xs[]: required에 없는 칸 y"),
    (obj({"e": {"type": "string", "enum": [str(i) for i in range(1001)]}}), "$.e: enum 값이 1001개로 1000개를 넘습니다"),
])
def test_shape_rules(tmp_path, schema, part):
    m = msgs(pack(tmp_path, "strict: true", schema))
    assert len(m) == 1 and part in m[0]


def test_nesting_deeper_than_ten_is_refused():
    s = {"type": "string"}
    for i in range(11):
        s = obj({f"l{i}": s})
    errors, _ = strict_schema_problems(s)
    assert any("object 깊이가 10을 넘습니다" in e for e in errors)
    s10 = {"type": "string"}
    for i in range(10):
        s10 = obj({f"l{i}": s10})
    assert strict_schema_problems(s10) == ([], [])


def test_more_than_5000_properties_are_refused():
    errors, _ = strict_schema_problems(obj({f"p{i}": {"type": "string"} for i in range(5001)}))
    assert errors == ["$: 속성이 모두 5001개로 5000개를 넘습니다"]


def test_chg36_keywords_are_not_said_twice(tmp_path):
    schema = obj({"a": {"type": "string", "not": {"const": "x"}}})
    m = msgs(pack(tmp_path, "strict: true", schema))
    assert len(m) == 1 and "구조화 출력이 받지 않는 키워드가 있습니다: not" in m[0]
    assert strict_schema_problems(schema)[0] == ["$.a: not는 구조화 출력이 받지 않는 키워드"]


def test_undocumented_keywords_are_a_warning_with_path_and_keyword(tmp_path):
    schema = obj({"xs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}, "d": {"type": "string", "default": "x"}},
                 definitions={"k": {"type": "string"}})
    res = pack(tmp_path, "strict: true", schema)
    assert res.ok and msgs(res, "warnings") == [
        "node 'draft'의 output 스키마 Answer에 strict 구조화 출력 문서로 확인하지 못한 키워드가 있습니다: $.definitions, $.xs.uniqueItems, "
        "$.d.default — 실제 호출에서 거부될 수 있습니다 (schemas/answer.json)"]


def test_annotations_and_documented_keywords_are_quiet():
    schema = obj({"s": {"type": "string", "pattern": "^a", "format": "date", "minLength": 1, "maxLength": 9, "description": "d"},
                  "n": {"type": ["number", "null"], "minimum": 0, "maximum": 1, "multipleOf": 0.5},
                  "c": {"const": "x"}, "r": {"$ref": "#/$defs/R"},
                  "u": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "xs": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3}},
                 title="T", description="D", **{"$schema": "draft-2020-12", "$id": "x", "$defs": {"R": {"type": "string", "enum": ["a"]}}})
    assert strict_schema_problems(schema) == ([], [])


def test_the_public_function_reads_only_the_dict():
    assert strict_schema_problems("x") == (["$: schema가 object가 아닙니다"], [])
    assert strict_schema_problems(obj({"a": {"type": "string"}})) == ([], [])


def test_errors_beyond_ten_are_counted(tmp_path):
    schema = obj({f"o{i}": {"type": "object", "properties": {}} for i in range(12)})
    m = msgs(pack(tmp_path, "strict: true", schema))
    assert len(m) == 1 and " 외 2개" in m[0]


def test_version_is_the_release_with_strict_and_seed():
    assert packrt.__version__ == "0.15.0"   # D-167: strict + seed came in 0.10.1; D-179: 0.11.0 = predict@1 · model release checks (EV-003); EV-006: 0.12.0 = retrieve@2 · packrt.index; EV-008: 0.12.1 = pgvector retrieve@2 · find_corpus · index load; EV-011: 0.12.2 = index build key from PACKRT_ENV_FILE; EV-013: 0.12.3 = env_fallback second key; EV-014: 0.12.4 = tracewriter 0.1.4 check records (packrt code as 0.12.3); EV-015: 0.12.5 = retrieve@2 rerank.pool; EV-016: 0.12.6 = packrt.ir.fixed_flow · check_fixed_flow; D-462: 0.13.0 = fixed-flow item checks · patch.bridge_ops · remove_unreachable; 0.13.1 = checks: ways no model output reaches are free · no requirement keeps the old rules items; 0.13.2 = codesig: custom rule signature the same on every Python version; D-475: 0.14.0 = llm reasoning_effort · models.yaml reasoning list; D-476: 0.15.0 = custom_code options · set_option · node_options · fixed-flow options item
