"""Fixed-flow list and structure check (EV-016, llmops EV-069 PK-1, D-101 ~ D-106 · D-433): packrt.ir.fixed_flow(folder) and
packrt.ir.check_fixed_flow(items, folder). Read only: no network, no DB, no Pack code run."""
import copy
import json
import shutil
from pathlib import Path

import pytest

import packrt.ir
from packrt.ir import check_fixed_flow, fixed_flow
from packrt.ir.patch import apply_patch, delete_impact

FIX = Path(__file__).parent / "fixtures"
MIN = FIX / "fixed_flow_min"
TASK10 = FIX / "task10_min"
KINDS = {"input", "output", "stores", "models", "evidence", "human", "rules", "checks", "requests", "permission", "side_effects"}
DRAFT = 'rule {"schema":"Draft"}'
ITEM_KEYS = {"id", "kind", "what", "basis", "value"}
VIOLATION_KEYS = {"item", "kind", "what", "found", "message"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def copy_pack(src, tmp_path, name="cand"):
    out = tmp_path / name
    shutil.copytree(src, out)
    return out


def edited(tmp_path, old, new, rel="pack.yaml", src=MIN):
    """A copy of src with one exact text replaced in one file (things apply_patch does not do: env, capabilities, schemas, reads)."""
    out = copy_pack(src, tmp_path)
    path = out / rel
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")
    return out


def patched(tmp_path, ops, src=MIN):
    out = tmp_path / "patched"
    res = apply_patch(src, ops, out_dir=out)
    assert res.ok, res.errors
    return out


def removed(tmp_path, node, bridge, src=MIN, graph="run"):
    """Remove a node with delete_impact's ops plus the edge that joins its neighbours again (a chain is not a join)."""
    impact = delete_impact(src, graph, node=node)
    return patched(tmp_path, impact["ops"] + [{"op": "add_edge", "graph": graph, "edge": list(bridge)}], src)


@pytest.fixture(scope="module")
def base():
    return fixed_flow(MIN)


def by_id(result):
    return {i["id"]: i for i in result["items"]}


# --- the list -----------------------------------------------------------------------------------------------------------------

def test_the_list_of_the_small_pack(base):
    assert base["errors"] == []
    assert base["pack"] == {"id": "ff_min", "version": "V1"}
    assert base["revision"].startswith("sha256:") and base["revision"] == packrt.ir.load_pack(MIN).revision
    assert [i["id"] for i in base["items"]] == [
        "checks:run", "evidence:history", "human:review", "input", "models:kind_classifier", "output", "permission", "requests",
        "side_effects", "stores:history", "stores:notes"]
    items = by_id(base)
    assert items["requests"]["value"] == {"run": {"router": "route_request", "values": ["alarm", "default", "start"]}}
    assert items["permission"]["value"] == {"network": "none", "side_effect": "none"}
    assert items["side_effects"]["value"] == {"env": ["OPENAI_API_KEY", "OPENAI_BASE_URL"], "tools": []}
    assert items["models:kind_classifier"]["value"] == {"model": "kind_classifier", "graphs": ["run"]}
    assert items["evidence:history"]["value"] == {"store": "history", "to": {"run": "result"}}
    assert items["input"]["value"]["run"]["schema"] == "Request" and items["output"]["value"]["run"]["schema"] == "Result"
    # D-462 ①: the last check before the output (draft_check, after the last llm tidy) per request kind; evidence_check is free
    assert items["checks:run"]["basis"] == "graphs/run/nodes/draft_check"
    assert items["checks:run"]["value"] == {"graph": "run", "classes": {k: {"mode": "after_model", "need": [[DRAFT]]}
                                                                        for k in ("alarm", "default", "start")}}
    assert items["human:review"]["basis"] == "graphs/run/nodes/review"
    assert items["stores:notes"]["basis"] == "graphs/run/nodes/find_notes"


def test_a_store_that_does_not_reach_the_output_is_kept_but_has_no_evidence_item(base):
    """notes is read (stores:notes) but only lands in notes_found, which nothing reads — like c1a15 manuals (trace only)."""
    ids = {i["id"] for i in base["items"]}
    assert "stores:notes" in ids and "evidence:notes" not in ids


def test_the_list_of_the_task10_pack():
    res = fixed_flow(TASK10)
    assert res["errors"] == [] and res["pack"] == {"id": "task10", "version": "V0"}
    items = by_id(res)
    assert sorted(items) == ["checks:respond", "evidence:history", "evidence:manuals", "human:review", "input",
                             "models:failure_classifier", "output", "permission", "side_effects", "stores:history", "stores:manuals"]
    assert sorted(items["input"]["value"]) == ["ingest", "respond"]
    assert items["output"]["value"]["respond"]["schema"] == "MaintenanceReport"
    assert items["evidence:manuals"]["value"] == {"store": "manuals", "to": {"respond": "report"}}
    assert items["permission"]["value"] == {"network": "allowlisted", "side_effect": "none"}
    assert items["side_effects"]["value"] == {"env": ["OPENAI_API_KEY"], "tools": []}
    assert "requests" not in items   # START goes to predict_failure, not to a router


def test_every_item_has_the_contract_shape(base):
    for res in (base, fixed_flow(TASK10)):
        ids = [i["id"] for i in res["items"]]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
        for item in res["items"]:
            assert set(item) == ITEM_KEYS and item["kind"] in KINDS
            assert item["id"] == item["kind"] or item["id"].startswith(item["kind"] + ":")
            assert isinstance(item["what"], str) and item["what"] and isinstance(item["basis"], str) and item["basis"]


def test_same_input_same_bytes_and_no_folder_path_in_the_list(tmp_path, base):
    again = fixed_flow(MIN)
    elsewhere = fixed_flow(copy_pack(MIN, tmp_path))
    assert dumps(again) == dumps(base) == dumps(elsewhere)
    assert json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == \
        json.dumps(elsewhere, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert str(MIN) not in dumps(base) and str(tmp_path) not in dumps(elsewhere)


def test_a_pack_that_does_not_load_has_errors_and_no_items(tmp_path):
    broken = edited(tmp_path, "ir_version: 1", "ir_version: 1\nnot_a_key: 1")
    res = fixed_flow(broken)
    assert res["items"] == [] and res["errors"] and res["revision"] is None
    assert res["errors"][0]["stage"] == "schema"
    empty = tmp_path / "empty"
    empty.mkdir()
    res = fixed_flow(empty)
    assert res == {"pack": None, "revision": None, "items": [], "errors": res["errors"]} and res["errors"][0]["stage"] == "read"


def test_reading_writes_nothing(tmp_path):
    folder = copy_pack(MIN, tmp_path)
    before = sorted((p.relative_to(folder).as_posix(), p.read_bytes()) for p in folder.rglob("*") if p.is_file())
    check_fixed_flow(fixed_flow(folder)["items"], folder)
    after = sorted((p.relative_to(folder).as_posix(), p.read_bytes()) for p in folder.rglob("*") if p.is_file())
    assert before == after


# --- the check: candidates that keep the flow ---------------------------------------------------------------------------------

def test_the_same_pack_and_a_byte_copy_pass(tmp_path, base):
    assert check_fixed_flow(base["items"], MIN) == []
    assert check_fixed_flow(base["items"], copy_pack(MIN, tmp_path)) == []
    t = fixed_flow(TASK10)
    assert check_fixed_flow(t["items"], TASK10) == []


def test_a_model_swap_passes(tmp_path, base):
    cand = patched(tmp_path, [{"op": "set", "graph": "run", "node": "draft", "param": "model", "value": "gpt-4.1-nano"},
                              {"op": "set", "graph": "run", "node": "tidy", "param": "model", "value": "gpt-4.1-nano"}])
    assert check_fixed_flow(base["items"], cand) == []


def test_removing_a_middle_standard_node_that_keeps_the_flow_passes(tmp_path, base):
    cand = removed(tmp_path, "tidy", ("draft", "draft_check"))
    assert "tidy" not in (cand / "pack.yaml").read_text(encoding="utf-8")
    assert check_fixed_flow(base["items"], cand) == []


def test_prompt_text_and_more_request_kinds_pass(tmp_path, base):
    cand = patched(tmp_path, [{"op": "write_file", "path": "prompts/draft.md", "text": "근거만으로 짧게 답하세요.\n근거: {state.evidence}\n"},
                              {"op": "set", "graph": "run", "node": "route_request", "param": "cases",
                               "value": {"alarm": "predict_kind", "start": "predict_kind", "judge": "predict_kind"}}])
    assert check_fixed_flow(base["items"], cand) == []


def test_lower_permission_and_fewer_env_names_pass(tmp_path, base):
    cand = edited(tmp_path, "optional: [OPENAI_BASE_URL]", "optional: []")
    assert check_fixed_flow(base["items"], cand) == []


# --- the check: one candidate per kind breaks exactly that item ---------------------------------------------------------------

def only(base, cand):
    found = check_fixed_flow(base["items"], cand)
    for v in found:
        assert set(v) == VIOLATION_KEYS
        item = by_id(base)[v["item"]]
        assert v["kind"] == item["kind"] and v["what"] == item["what"]
        assert v["message"] == f"고정 흐름 '{item['what']}'을 어김 — 후보에서는 {v['found']}"
        assert "볼 수 없음" not in v["found"]   # a real item is compared, not caught by the item-shape fallback
    return [v["item"] for v in found]


def test_input_schema_change_breaks_input_only(tmp_path, base):
    cand = edited(tmp_path, '"text": {"type": "string"}}}', '"text": {"type": "string"}, "extra": {"type": "string"}}}',
                  rel="schemas/request.json")
    assert only(base, cand) == ["input"]


def test_output_schema_change_breaks_output_only(tmp_path, base):
    cand = edited(tmp_path, '"required": ["text"]', '"required": []', rel="schemas/result.json")
    assert only(base, cand) == ["output"]


def test_dropping_a_store_breaks_that_store_only(tmp_path, base):
    assert only(base, removed(tmp_path, "find_notes", ("find_history", "evidence_check"))) == ["stores:notes"]


def test_a_store_that_no_longer_reaches_the_output_breaks_evidence_only(tmp_path, base):
    cand = edited(tmp_path, "reads: [request, evidence], writes: [draft]", "reads: [request], writes: [draft]")
    (cand / "prompts" / "draft.md").write_text("요청만으로 답을 쓰세요.\n요청: {state.request.text}\n", encoding="utf-8")
    assert packrt.ir.load_pack(cand).ok
    assert only(base, cand) == ["evidence:history"]


def test_a_store_changed_in_its_definition_breaks_that_store(tmp_path, base):
    cand = edited(tmp_path, "history: {type: table, schema: Record, key: record_id}", "history: {type: table, schema: Request, key: kind}")
    assert only(base, cand) == ["stores:history"]


def test_another_predict_model_breaks_models_only(tmp_path, base):
    cand = edited(tmp_path, "model: kind_classifier,", "model: kind_classifier_2,")
    assert only(base, cand) == ["models:kind_classifier"]


def test_a_changed_human_gate_breaks_human_only(tmp_path, base):
    cand = patched(tmp_path, [{"op": "set", "graph": "run", "node": "review", "param": "routes",
                               "value": {"approve": "finish", "reject": "finish"}}])
    assert only(base, cand) == ["human:review"]


def test_a_removed_human_gate_breaks_human(tmp_path, base):
    cand = edited(tmp_path, "id: review\n        kind: human_review@1\n        params: {subject: draft, actions: [approve, reject], "
                            "routes: {approve: finish, reject: END}}\n        reads: [draft, draft_check]\n        writes: [review]",
                  "id: review\n        kind: render@1\n        params: {template: templates/result.md, format: md, to: review}\n"
                  "        reads: [draft, draft_check]\n        writes: [review]")
    text = (cand / "pack.yaml").read_text(encoding="utf-8").replace("      review: dict\n", "      review: Result\n") \
        .replace("[draft_check, review]", "[draft_check, review]\n      - [review, finish]")
    (cand / "pack.yaml").write_text(text, encoding="utf-8")
    assert packrt.ir.load_pack(cand).ok, packrt.ir.load_pack(cand).errors
    assert only(base, cand) == ["human:review"]


def test_a_check_that_is_not_the_last_one_is_free(tmp_path, base):
    """D-462 ①: only the last check before the output counts — evidence_check (before the llm nodes) may change or go."""
    cand = patched(tmp_path, [{"op": "add_rule", "graph": "run", "node": "evidence_check", "rule": {"required": ["evidence"]}}])
    assert only(base, cand) == []
    assert only(base, removed(tmp_path / "r", "evidence_check", ("find_notes", "draft"))) == []


def test_a_stricter_last_check_keeps_and_another_rule_breaks_checks_only(tmp_path, base):
    stricter = patched(tmp_path, [{"op": "add_rule", "graph": "run", "node": "draft_check", "rule": {"required": ["draft"]}}])
    assert only(base, stricter) == []
    other = patched(tmp_path / "o", [{"op": "set", "graph": "run", "node": "draft_check", "param": "rules[0].schema", "value": "Result"}])
    v = check_fixed_flow(base["items"], other)
    assert [x["item"] for x in v] == ["checks:run"] and v[0]["found"].endswith("규칙 schema 빠짐")


def test_a_request_kind_that_is_no_longer_taken_breaks_requests_only(tmp_path, base):
    cand = patched(tmp_path, [{"op": "set", "graph": "run", "node": "route_request", "param": "cases", "value": {"alarm": "predict_kind"}}])
    v = check_fixed_flow(base["items"], cand)
    assert [x["item"] for x in v] == ["requests"] and "start" in v[0]["found"]


def test_higher_permission_breaks_permission_only(tmp_path, base):
    cand = edited(tmp_path, "capabilities: {network: none, side_effect: none}", "capabilities: {network: none, side_effect: dry_run}")
    assert packrt.ir.load_pack(cand).ok, packrt.ir.load_pack(cand).errors
    assert only(base, cand) == ["permission"]


def test_a_new_env_name_breaks_side_effects_only(tmp_path, base):
    cand = edited(tmp_path, "optional: [OPENAI_BASE_URL]", "optional: [OPENAI_BASE_URL, OTHER_TOKEN]")
    v = check_fixed_flow(base["items"], cand)
    assert [x["item"] for x in v] == ["side_effects"] and "OTHER_TOKEN" in v[0]["found"]


def test_violations_come_in_item_order(tmp_path, base):
    cand = edited(tmp_path, "optional: [OPENAI_BASE_URL]", "optional: [OPENAI_BASE_URL, OTHER_TOKEN]")
    text = (cand / "pack.yaml").read_text(encoding="utf-8").replace("model: kind_classifier,", "model: kind_classifier_2,")
    (cand / "pack.yaml").write_text(text, encoding="utf-8")
    assert only(base, cand) == ["models:kind_classifier", "side_effects"]


# --- the check: load failure, bad items, items not changed --------------------------------------------------------------------

def test_a_candidate_that_does_not_load_is_one_load_violation(tmp_path, base):
    broken = edited(tmp_path, "ir_version: 1", "ir_version: 1\nnot_a_key: 1")
    v = check_fixed_flow(base["items"], broken)
    assert len(v) == 1 and v[0]["item"] == "load" and v[0]["kind"] == "load" and set(v[0]) == VIOLATION_KEYS
    assert "not_a_key" in v[0]["found"] and v[0]["message"].startswith("후보 Pack을 불러오지 못함 — ")
    v = check_fixed_flow(base["items"], tmp_path / "missing")
    assert [x["item"] for x in v] == ["load"]


def test_an_item_the_check_does_not_know_is_a_violation(base):
    bad = [{"id": "secret_rule", "kind": "secret_rule", "what": "모르는 항목", "basis": "x", "value": {}}]
    v = check_fixed_flow(bad, MIN)
    assert [(x["item"], x["kind"], x["found"]) for x in v] == [("secret_rule", "secret_rule", "이 검사가 모르는 항목이라 지켰는지 볼 수 없음")]
    v = check_fixed_flow([{"id": "stores:history", "kind": "stores"}], MIN)
    assert [(x["item"], x["found"]) for x in v] == [("stores:history", "항목 모양이 달라 지켰는지 볼 수 없음")]
    v = check_fixed_flow(["not an item"], MIN)
    assert [x["item"] for x in v] == ["?"]


def test_the_check_does_not_change_the_items(tmp_path, base):
    items = copy.deepcopy(base["items"])
    check_fixed_flow(items, removed(tmp_path, "find_notes", ("find_history", "evidence_check")))
    assert items == base["items"]


def test_no_banned_words_in_what_or_messages(tmp_path, base):
    words = ("판", "검색", "색인")
    texts = [i["what"] for i in base["items"] + fixed_flow(TASK10)["items"]]
    cand = edited(tmp_path, "optional: [OPENAI_BASE_URL]", "optional: [OPENAI_BASE_URL, OTHER_TOKEN]")
    texts += [v["message"] for v in check_fixed_flow(base["items"], cand)]
    texts += [v["message"] for v in check_fixed_flow(base["items"], tmp_path / "missing")]
    assert texts and not [t for t in texts if any(w in t for w in words)]


# --- custom rule files and tools (not in the small fixture) -------------------------------------------------------------------

CUSTOM_RULE = ("id: draft_check\n        kind: validate@1\n        params: {rules: [{schema: Draft}]}",
               "id: draft_check\n        kind: validate@2\n        params: {rules: [{custom: \"custom.checks:draft_ok\"}]}")
TOOL = ("trace: {contract: 1}", "tools:\n  lookup: {effect: read, input: Request, output: Draft, capabilities: {network: allowlisted, "
                              "side_effect: none}, adapters: {mock: \"custom.tools:lookup\"}}\ntrace: {contract: 1}")


def with_custom(tmp_path, name, rule_body="return True", tool=False):
    folder = edited(tmp_path / name, *CUSTOM_RULE) if not tool else edited(tmp_path / name, *TOOL)
    (folder / "custom").mkdir(exist_ok=True)
    (folder / "custom" / "checks.py").write_text(f"def draft_ok(state):\n    {rule_body}\n", encoding="utf-8")
    (folder / "custom" / "tools.py").write_text("def lookup(value):\n    return {\"answer\": \"\"}\n", encoding="utf-8")
    res = packrt.ir.load_pack(folder)
    assert res.ok, res.errors
    return folder


def test_a_custom_rule_counts_its_function_file(tmp_path):
    base = with_custom(tmp_path, "a")
    items = fixed_flow(base)["items"]
    assert check_fixed_flow(items, with_custom(tmp_path, "b")) == []
    changed = with_custom(tmp_path, "c", rule_body="return False")
    v = check_fixed_flow(items, changed)
    assert [x["item"] for x in v] == ["checks:run"] and v[0]["found"].startswith("alarm 길 predict_kind → ")
    assert v[0]["found"].endswith("원래 마지막 검사와 같거나 더 엄한 검사가 없음 — 함수 custom.checks:draft_ok 몸이 다름")


def test_tools_are_listed_and_a_new_tool_breaks_side_effects(tmp_path, base):
    folder = with_custom(tmp_path, "t", tool=True)
    items = by_id(fixed_flow(folder))
    assert items["side_effects"]["value"] == {"env": ["OPENAI_API_KEY", "OPENAI_BASE_URL"], "tools": ["lookup"]}
    assert items["permission"]["value"] == {"network": "allowlisted", "side_effect": "none"}   # a tool's capabilities count
    assert [x["item"] for x in check_fixed_flow(fixed_flow(folder)["items"], folder)] == []
    v = check_fixed_flow(base["items"], folder)
    assert [(x["item"], x["found"]) for x in v] == [("permission", "권한이 올라감: network allowlisted"),
                                                     ("side_effects", "새 이름: tools lookup")]


def test_a_function_node_that_names_a_store_reads_it(tmp_path):
    """custom_code@3 params.stores (task10-pack custom.history:search) counts as reading the store, like retrieve params.store."""
    old = ("id: find_notes\n        kind: retrieve@1\n        params: {store: notes, where: {kind: \"{state.prediction.type}\"}, limit: 3}\n")
    new = ("id: find_notes\n        kind: custom_code@3\n        params: {fn: \"custom.notes:find\", stores: [notes, history]}\n"
           "        capabilities: {network: none, side_effect: none}\n")
    folder = edited(tmp_path, old, new)
    (folder / "custom").mkdir()
    (folder / "custom" / "notes.py").write_text("def find(state):\n    return {\"notes_found\": []}\n", encoding="utf-8")
    assert packrt.ir.load_pack(folder).ok, packrt.ir.load_pack(folder).errors
    items = by_id(fixed_flow(folder))
    assert items["stores:notes"]["basis"] == "graphs/run/nodes/find_notes"
    assert items["stores:history"]["basis"] == "graphs/run/nodes/find_history · graphs/run/nodes/find_notes"
    assert "evidence:notes" not in items
    dropped = copy_pack(folder, tmp_path, "dropped")
    text = (dropped / "pack.yaml").read_text(encoding="utf-8")
    (dropped / "pack.yaml").write_text(text.replace("stores: [notes, history]", "stores: [history]"), encoding="utf-8")
    v = check_fixed_flow(fixed_flow(folder)["items"], dropped)
    assert [(x["item"], x["found"]) for x in v] == [("stores:notes", "데이터 notes를 읽는 node가 없음")]


def test_the_same_schema_under_another_name_breaks_input(tmp_path, base):
    cand = edited(tmp_path, "  Request: schemas/request.json\n", "  Request: schemas/request.json\n  Ask: schemas/request.json\n")
    text = (cand / "pack.yaml").read_text(encoding="utf-8").replace("      request: Request\n", "      request: Ask\n") \
        .replace("input: Request,", "input: Ask,")
    (cand / "pack.yaml").write_text(text, encoding="utf-8")
    assert packrt.ir.load_pack(cand).ok, packrt.ir.load_pack(cand).errors
    v = check_fixed_flow(base["items"], cand)
    assert [(x["item"], x["found"]) for x in v] == [("input", "run의 입력 형식이 Ask")]


# --- two entry graphs (critic E3-A1 B1 · B2): every item is kept in each graph that had it -----------------------------------

SIDE = '''  side:
    state:
      request: Request
      prediction: Prediction
      notes_found: {type: "list[EvidenceRef]", merge: append, order_by: [source_type, score_desc, id]}
      draft: Draft
      draft_check: dict
      review: dict
      result: Result
    nodes:
      - {id: predict_side, kind: predict@1, params: {model: kind_classifier, features: {from: request}, output: Prediction}, reads: [request], writes: [prediction]}
      - id: look_notes
        kind: retrieve@1
        params: {store: notes, where: {kind: "{state.prediction.type}"}, limit: 3}
        reads: [prediction]
        writes: [notes_found]
      - {id: side_draft, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/side.md, output: Draft}, reads: [request, notes_found], writes: [draft]}
      - id: side_check
        kind: validate@1
        params: {rules: [{schema: Draft}]}
        reads: [draft]
        writes: [draft_check]
      - id: review
        kind: human_review@1
        params: {subject: draft, actions: [approve, edit, reject], routes: {approve: side_finish, edit: side_finish, reject: END}}
        reads: [draft]
        writes: [review]
      - {id: side_finish, kind: render@1, params: {template: templates/result.md, format: md, to: result}, reads: [request, draft, review], writes: [result]}
    edges:
      - [START, predict_side]
      - [predict_side, look_notes]
      - [look_notes, side_draft]
      - [side_draft, side_check]
      - [side_check, review]
      - [side_finish, END]
'''


@pytest.fixture()
def two(tmp_path):
    folder = edited(tmp_path / "two", "entrypoints:\n", SIDE + "entrypoints:\n")
    text = (folder / "pack.yaml").read_text(encoding="utf-8").replace(
        "  - {graph: run, input: Request, output: Result}\n",
        "  - {graph: run, input: Request, output: Result}\n  - {graph: side, input: Request, output: Result}\n")
    (folder / "pack.yaml").write_text(text, encoding="utf-8")
    (folder / "prompts" / "side.md").write_text("메모만으로 답을 쓰세요.\n메모: {state.notes_found}\n", encoding="utf-8")
    assert packrt.ir.load_pack(folder).ok, packrt.ir.load_pack(folder).errors
    return folder


def test_two_entry_graphs_are_listed_per_graph(two):
    items = by_id(fixed_flow(two))
    assert items["stores:notes"]["value"]["graphs"] == ["run", "side"]
    assert items["models:kind_classifier"]["value"]["graphs"] == ["run", "side"]
    assert items["checks:side"]["value"] == {"graph": "side", "classes": {"*": {"mode": "after_model", "need": [[DRAFT]]}}}
    assert items["checks:run"]["basis"] == "graphs/run/nodes/draft_check" and items["checks:side"]["basis"] == "graphs/side/nodes/side_check"
    assert sorted(items["human:review"]["value"]["graphs"]) == ["run", "side"]
    assert items["evidence:notes"]["value"]["to"] == {"side": "result"}
    assert check_fixed_flow(fixed_flow(two)["items"], two) == []


def test_a_human_gate_changed_in_the_other_graph_breaks_human(tmp_path, two):
    items = fixed_flow(two)["items"]
    cand = patched(tmp_path, [{"op": "set", "graph": "side", "node": "review", "param": "routes",
                               "value": {"approve": "side_finish", "edit": "side_finish", "reject": "side_finish"}}], src=two)
    v = check_fixed_flow(items, cand)
    assert [(x["item"], x["found"]) for x in v] == [("human:review", "사람 관문 review의 설정이 side에서 다름")]


def test_a_store_dropped_in_one_graph_breaks_it_even_if_another_graph_still_reads_it(tmp_path, two):
    items = fixed_flow(two)["items"]
    cand = edited(tmp_path, "      - id: look_notes\n        kind: retrieve@1\n        params: {store: notes,",
                  "      - id: look_notes\n        kind: retrieve@1\n        params: {store: history,", src=two)
    assert packrt.ir.load_pack(cand).ok, packrt.ir.load_pack(cand).errors
    v = check_fixed_flow(items, cand)
    assert [(x["item"], x["found"]) for x in v] == [("evidence:notes", "데이터 notes가 side의 출력에 닿지 않음"),
                                                     ("stores:notes", "데이터 notes를 읽는 node가 side에 없음")]


def test_a_rule_dropped_in_one_graph_breaks_it(tmp_path, two):
    items = fixed_flow(two)["items"]
    cand = removed(tmp_path, "side_check", ("side_draft", "review"), src=two, graph="side")
    v = check_fixed_flow(items, cand)
    assert [(x["item"], x["found"]) for x in v] == [
        ("checks:side", "* 길 predict_side → look_notes → side_draft → review → side_finish: 모델 node side_draft 뒤 검사 없이 출력")]


def test_a_graph_without_an_entrypoint_does_not_count(tmp_path):
    """A graph no entrypoint names is never run (engine: the entry graph only), so a read there keeps nothing."""
    dead = SIDE.replace("  side:\n", "  dead:\n")
    folder = edited(tmp_path / "dead", "entrypoints:\n", dead + "entrypoints:\n")
    (folder / "prompts" / "side.md").write_text("메모만으로 답을 쓰세요.\n메모: {state.notes_found}\n", encoding="utf-8")
    res = packrt.ir.load_pack(folder)
    assert res.ok, res.errors
    items = by_id(fixed_flow(folder))
    assert items["stores:notes"]["value"]["graphs"] == ["run"] and "graphs/dead" not in dumps(items)


def test_the_first_entrypoint_of_a_graph_and_the_first_output_key_count(tmp_path):
    """Runtime: the first entrypoint of a graph, and the first state key (state order) whose type is the output (critic N1 · N2)."""
    folder = edited(tmp_path / "ep", "  - {graph: run, input: Request, output: Result}\n",
                    "  - {graph: run, input: Request, output: Result}\n  - {graph: run, input: Request, output: Draft}\n")
    assert packrt.ir.load_pack(folder).ok, packrt.ir.load_pack(folder).errors
    items = by_id(fixed_flow(folder))
    assert items["output"]["value"]["run"]["schema"] == "Result"
    assert items["evidence:history"]["value"]["to"] == {"run": "result"}


def test_a_predict_model_changed_in_one_graph_breaks_it(tmp_path, two):
    items = fixed_flow(two)["items"]
    cand = edited(tmp_path, "id: predict_side, kind: predict@1, params: {model: kind_classifier,",
                  "id: predict_side, kind: predict@1, params: {model: kind_classifier_2,", src=two)
    v = check_fixed_flow(items, cand)
    assert [(x["item"], x["found"]) for x in v] == [("models:kind_classifier", "예측 모델 kind_classifier를 쓰는 node가 side에 없음")]
