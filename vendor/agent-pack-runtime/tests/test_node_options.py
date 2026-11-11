"""packrt 0.15.0(D-476 · 보고 151 Q-1 (가) · Q-2 (가) · Q-3 (나)): 함수 node(custom_code@3) 옵션 선언 — params.options에 이름마다
형(choice · number · integer · switch) · 한도 · 지금 값 · label. 선언한 node만 함수가 options= 하나로 지금 값을 받고, trace request_config에
남음. 값은 patch op set_option으로 한도 안에서만 바꿈(선언은 못 바꿈). 고정 흐름 options:<graph> 항목 = 선언(값 빼고). 선언 없는
Pack은 0.14.0과 같음. 실제 호출 0."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from packrt.ir import check_fixed_flow, fixed_flow, load_pack, node_options
from packrt.ir.patch import apply_patch
from packrt.run.engine import Runtime

ANSWER = (Path(__file__).resolve().parent / "fixtures" / "llm_min" / "schemas" / "answer.json").read_text(encoding="utf-8")
TICKET = '{"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}'
OPTS = ('{score_rule: {type: choice, choices: [sum, rank_weighted, near_only], value: sum, label: "판정 셈"}, '
        'base_score: {type: number, min: 0, max: 1, step: 0.05, value: 0.5, loosen: up}, '
        'top_n: {type: integer, min: 1, max: 20, value: 10}, '
        'use_manual: {type: switch, value: false}}')
FN_OPT = '''def judge(ticket, options):
    """옵션 지금 값을 그대로 돌려줌."""
    seen = dict(options)
    options["score_rule"] = "changed"   # 함수가 바꿔도 다음 Run에 남지 않아야 함
    return {"answer": {"summary": json.dumps(seen, sort_keys=True), "category": "other", "confidence": seen.get("base_score")}}
'''
FN_PLAIN = '''def judge(ticket):
    return {"answer": {"summary": "plain", "category": "other", "confidence": None}}
'''


def make(tmp_path, options=OPTS, fn=FN_OPT, reads="[ticket]", name="p"):
    work = tmp_path / name
    (work / "schemas").mkdir(parents=True)
    (work / "custom").mkdir()
    (work / "schemas" / "answer.json").write_text(ANSWER, encoding="utf-8")
    (work / "schemas" / "ticket.json").write_text(TICKET, encoding="utf-8")
    (work / "custom" / "__init__.py").write_text("", encoding="utf-8")
    (work / "custom" / "judge.py").write_text("import json\n\n\n" + fn, encoding="utf-8")
    params = '{fn: "custom.judge:judge"' + (f", options: {options}" if options is not None else "") + "}"
    extra_state = "      options: Answer\n" if "options" in reads else ""
    (work / "pack.yaml").write_text(f"""ir_version: 1
runtime_contract: {{min_runtime_version: 0.10.0}}
pack: {{id: opt_min, version: V0, title: "옵션 시험"}}
env: {{required: [], optional: []}}
schemas:
  Ticket: schemas/ticket.json
  Answer: schemas/answer.json
graphs:
  ask:
    state:
      ticket: Ticket
      answer: Answer
{extra_state}    nodes:
      - id: judge
        kind: custom_code@3
        params: {params}
        capabilities: {{network: none, side_effect: none}}
        reads: {reads}
        writes: [answer]
    edges: [[START, judge], [judge, END]]
entrypoints:
  - {{graph: ask, input: Ticket, output: Answer}}
trace: {{contract: 1}}
""", encoding="utf-8")
    return work


def errs(res):
    return [p["message"] for p in res.errors]


class Rec:
    def __init__(self):
        self.events, self.n = [], 0

    def put_payload(self, value, *, role, retention_class):
        self.n += 1
        return {"ref": "sha256:" + f"{self.n:064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


def run(work):
    roots = work.parent / f"releases-{work.name}"
    roots.mkdir(exist_ok=True)
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: ask\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n', encoding="utf-8")
    rec = Rec()
    res = Runtime(work, env={}).run({"id": "T-1", "text": "x"}, sink=rec, review={"policy": "auto_approve"})
    meta = [e["body"]["meta"] for e in rec.events if e["type"] == "node_ended" and e["node_id"] == "judge"]
    return res, (meta[0] if meta else None)


# ------------------------------------------------------------------ O1 칸
def test_o1_the_four_types_are_taken(tmp_path):
    res = load_pack(make(tmp_path))
    assert res.ok and errs(res) == [], errs(res)


@pytest.mark.parametrize("opts, says", [
    ("{a: {type: choice, choices: [x, y], value: z}}", "a"),                                  # 값이 choices 밖
    ("{a: {type: choice, choices: [x], value: x}}", "a"),                                     # choices 1개
    ("{a: {type: choice, choices: [x, x], value: x}}", "a"),                                  # 겹침
    ("{a: {type: choice, choices: [X, y], value: y}}", "a"),                                  # 꼴
    ("{a: {type: number, min: 1, max: 1, value: 1}}", "a"),                                   # min ≥ max
    ("{a: {type: number, min: 0, max: 1, value: 2}}", "a"),                                   # 한도 밖
    ("{a: {type: number, min: 0, max: 1, step: 0.25, value: 0.3}}", "a"),                     # step 어긋남
    ("{a: {type: number, max: 1, value: 0}}", "a"),                                           # min 없음
    ("{a: {type: integer, min: 0, max: 5, value: 2.5}}", "a"),                                # 정수 아님
    ("{a: {type: switch, value: 1}}", "a"),                                                   # 켜기 아님
    ("{a: {type: switch, value: true, note: x}}", "a"),                                       # 모르는 칸
    ("{a: {type: text, value: x}}", "a"),                                                     # 모르는 형
    ("{a: {type: switch, value: true, loosen: sideways}}", "a"),                              # loosen 값
    ("{Bad: {type: switch, value: true}}", "Bad"),                                            # 이름 꼴
    ("{a: {type: switch, value: true, label: " + "가" * 41 + "}}", "a"),                       # label 길이
])
def test_o1_bad_declarations_are_errors(tmp_path, opts, says):
    res = load_pack(make(tmp_path, opts))
    assert not res.ok and any(says in m or says in json.dumps(p.get("path"), ensure_ascii=False) for p in res.errors for m in [p["message"]])


def test_o1_seventeen_options_are_an_error(tmp_path):
    opts = "{" + ", ".join(f"o{i}: {{type: switch, value: true}}" for i in range(17)) + "}"
    res = load_pack(make(tmp_path, opts))
    assert not res.ok and any("16" in m for m in errs(res))


def test_o1_a_read_named_options_is_an_error(tmp_path):
    res = load_pack(make(tmp_path, reads="[ticket, options]"))
    assert not res.ok and any("options" in m and "겹" in m for m in errs(res))


# ------------------------------------------------------------------ O2 함수
def test_o2_a_function_without_options_is_an_error(tmp_path):
    res = load_pack(make(tmp_path, fn=FN_PLAIN))
    assert not res.ok and any("매개변수 options" in m for m in errs(res))


def test_o2_a_function_with_kw_is_taken(tmp_path):
    fn = FN_OPT.replace("def judge(ticket, options):", "def judge(ticket, **kw):\n    options = kw['options']")
    res = load_pack(make(tmp_path, fn=fn))
    assert res.ok, errs(res)


# ------------------------------------------------------------------ O3 실행 · O4 trace
def test_o3_the_function_gets_the_current_values_and_trace_keeps_them(tmp_path):
    work = make(tmp_path)
    want = {"base_score": 0.5, "score_rule": "sum", "top_n": 10, "use_manual": False}
    for _ in range(2):   # 함수가 바꾼 값이 다음 Run에 남지 않음
        res, meta = run(work)
        assert res["status"] == "succeeded", res
        assert json.loads(res["output"]["summary"]) == want and res["output"]["confidence"] == 0.5
        assert meta["request_config"] == {"options": want}


def test_o3_a_node_without_options_gets_no_keyword_and_records_nothing(tmp_path):
    res, meta = run(make(tmp_path, options=None, fn=FN_PLAIN))
    assert res["status"] == "succeeded" and res["output"]["summary"] == "plain"
    assert "request_config" not in meta or meta["request_config"] is None


# ------------------------------------------------------------------ O5 patch
def test_o5_set_option_inside_the_limits_makes_a_new_revision(tmp_path):
    base = make(tmp_path)
    out = tmp_path / "cand"
    res = apply_patch(base, [{"op": "set_option", "graph": "ask", "node": "judge", "option": "base_score", "value": 0.3},
                             {"op": "set_option", "graph": "ask", "node": "judge", "option": "score_rule", "value": "near_only"},
                             {"op": "set_option", "graph": "ask", "node": "judge", "option": "use_manual", "value": True}], out_dir=out)
    assert res.ok, res.errors
    assert load_pack(out).revision != load_pack(base).revision
    got = {o["name"]: o["value"] for o in node_options(out)}
    assert got == {"score_rule": "near_only", "base_score": 0.3, "top_n": 10, "use_manual": True}


@pytest.mark.parametrize("option, value", [("base_score", 1.5), ("base_score", 0.33), ("score_rule", "other"), ("top_n", 2.5),
                                           ("use_manual", "yes"), ("missing", 1)])
def test_o5_set_option_outside_the_limits_is_refused(tmp_path, option, value):
    res = apply_patch(make(tmp_path), [{"op": "set_option", "graph": "ask", "node": "judge", "option": option, "value": value}],
                      out_dir=tmp_path / "cand")
    assert not res.ok


def test_o5_set_cannot_change_the_declaration(tmp_path):
    res = apply_patch(make(tmp_path), [{"op": "set", "graph": "ask", "node": "judge", "param": "options", "value": {}}],
                      out_dir=tmp_path / "cand")
    assert not res.ok


# ------------------------------------------------------------------ O6 고정 흐름
def option_found(base, cand):
    return [v for v in check_fixed_flow(fixed_flow(base)["items"], cand) if v["kind"] == "options"]


def test_o6_the_item_holds_the_declaration_without_values(tmp_path):
    items = [i for i in fixed_flow(make(tmp_path))["items"] if i["kind"] == "options"]
    assert [i["id"] for i in items] == ["options:ask"]
    assert "value" not in json.dumps(items[0]["value"])


def test_o6_changing_values_only_keeps_the_item(tmp_path):
    base = make(tmp_path)
    cand = make(tmp_path, OPTS.replace("value: sum", "value: near_only").replace("value: 0.5,", "value: 0.25,"), name="c")
    assert option_found(base, cand) == []


@pytest.mark.parametrize("old, new", [
    ("max: 1, step", "max: 2, step"),                                      # 한도 넓힘
    ("[sum, rank_weighted, near_only]", "[sum, rank_weighted, near_only, all]"),  # 꼴 더함
    (", use_manual: {type: switch, value: false}", ""),                   # 옵션 뺌
    ('label: "판정 셈"', 'label: "셈"'),                                    # 이름표 바꿈
    ("loosen: up", "loosen: down"),                                        # 느슨한 쪽 바꿈
])
def test_o6_changing_the_declaration_is_a_violation(tmp_path, old, new):
    assert OPTS.count(old) == 1
    base = make(tmp_path)
    cand = make(tmp_path, OPTS.replace(old, new), name="c")
    assert len(option_found(base, cand)) == 1


def test_o6_a_pack_without_options_has_no_item(tmp_path):
    assert not [i for i in fixed_flow(make(tmp_path, options=None, fn=FN_PLAIN))["items"] if i["kind"] == "options"]


# ------------------------------------------------------------------ O8 node_options
def test_o8_node_options_lists_the_declaration(tmp_path):
    work = make(tmp_path)
    got = node_options(work)
    assert got == node_options(load_pack(work).doc)
    assert [o["name"] for o in got] == ["score_rule", "base_score", "top_n", "use_manual"]
    assert got[0] == {"graph": "ask", "node": "judge", "fn": "custom.judge:judge", "name": "score_rule", "type": "choice",
                      "choices": ["sum", "rank_weighted", "near_only"], "value": "sum", "label": "판정 셈", "loosen": "none"}
    assert got[1] == {"graph": "ask", "node": "judge", "fn": "custom.judge:judge", "name": "base_score", "type": "number",
                      "min": 0, "max": 1, "step": 0.05, "value": 0.5, "label": None, "loosen": "up"}


def test_o8_no_options_is_an_empty_list(tmp_path):
    assert node_options(make(tmp_path, options=None, fn=FN_PLAIN)) == []


# ------------------------------------------------------------------ O9 같은 그래프 같은 이름 = 한 값(D-476 덧 6)
SHARED = "{base_score: {type: number, min: 0, max: 1, step: 0.05, value: 0.5, loosen: up}}"


def make_two(tmp_path, first=SHARED, second=SHARED, name="two"):
    work = make(tmp_path, options=first, name=name)
    text = (work / "pack.yaml").read_text(encoding="utf-8")
    text = text.replace("      answer: Answer\n", "      answer: Answer\n      answer2: Answer\n", 1)
    text = text.replace("    edges: [[START, judge], [judge, END]]",
                        "      - id: judge2\n        kind: custom_code@3\n"
                        f'        params: {{fn: "custom.judge:judge", options: {second}}}\n'
                        "        capabilities: {network: none, side_effect: none}\n        reads: [ticket]\n        writes: [answer2]\n"
                        "    edges: [[START, judge], [judge, judge2], [judge2, END]]")
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    (work / "custom" / "judge.py").write_text("import json\n\n\n" + FN_OPT.replace('options["score_rule"] = "changed"', "options.clear()"), encoding="utf-8")
    return work


def test_o9_the_same_name_with_the_same_declaration_is_taken(tmp_path):
    res = load_pack(make_two(tmp_path))
    assert res.ok, errs(res)


@pytest.mark.parametrize("second", [SHARED.replace("value: 0.5", "value: 0.25"), SHARED.replace("max: 1", "max: 0.9"),
                                    SHARED.replace(", loosen: up", "")])
def test_o9_the_same_name_with_another_declaration_is_an_error(tmp_path, second):
    res = load_pack(make_two(tmp_path, second=second))
    assert not res.ok and any("base_score" in m and "한 값" in m for m in errs(res))


def test_o9_set_option_changes_every_place_in_the_graph(tmp_path):
    base = make_two(tmp_path)
    out = tmp_path / "cand"
    res = apply_patch(base, [{"op": "set_option", "graph": "ask", "node": "judge2", "option": "base_score", "value": 0.3}], out_dir=out)
    assert res.ok, res.errors
    assert [(o["node"], o["value"]) for o in node_options(out)] == [("judge", 0.3), ("judge2", 0.3)]
