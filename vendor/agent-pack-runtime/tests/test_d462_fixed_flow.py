"""D-462 ① 결과 검사 항목(kind checks) · ② 다시 잇기(bridge_ops) — 끝 기준 시험 1 · 2 · 3 과 지킴(T1 ~ T4, 보고 142 · 덧 1).

checks: 첫 router 갈래 값(요청 종류)마다, 출력에 닿는 길 하나하나에 '그 길의 마지막 모델 node 뒤 · 출력 쓰는 node 앞' 검사들의 규칙
서명 합이 원래 마지막 검사 서명 하나를 품어야 한다. 서명 = 규칙 칸 글, custom 규칙은 그 함수 몸 AST + 부르는 Pack 안 함수 · 상수 ·
클래스(같은 파일 · 모듈 별칭 속성 · from custom.x import), 못 따라가면 그 모듈 파일 전체. 다시 쓰기 길의 검사는 길과 같이 없어져도 된다.
bridge_ops: 빼는 node 묶음으로 들어오던 길(단순 · router · on_fail · 사람 관문 · join · loop)을 출구로 다시 잇고, 닿지 않게 된 node는
같이 뺀 뒤 apply_patch 흐름 검사. 실제 호출 0 — 가짜 provider로만 돈다."""
import json
import shutil
import sys
from pathlib import Path

import pytest

import packrt
from packrt.ir import check_fixed_flow, fixed_flow, load_pack
from packrt.ir.patch import apply_patch, bridge_ops
from packrt.run.engine import Runtime

FIX = Path(__file__).parent / "fixtures"
RW = FIX / "ff_rewrite"
MIN = FIX / "fixed_flow_min"
TASK10 = FIX / "task10_min"


def copy_pack(src, tmp_path, name="cand"):
    out = tmp_path / name
    shutil.copytree(src, out)
    return out


def edited(tmp_path, old, new, rel="pack.yaml", src=RW, name="cand"):
    out = copy_pack(src, tmp_path, name)
    path = out / rel
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")
    return out


def patched(tmp_path, ops, src=RW, name="patched"):
    out = tmp_path / name
    res = apply_patch(src, ops, out_dir=out)
    assert res.ok, res.errors
    return out


def bridged(tmp_path, nodes, exit=None, src=RW, graph="run"):
    b = bridge_ops(src, graph, nodes, exit=exit)
    assert b["after"]["ok"], (b["tried"], b["after"])
    return b, patched(tmp_path, b["ops"], src)


def graph_of(folder, graph="run"):
    g = load_pack(folder).doc["graphs"][graph]
    return {n["id"]: n for n in g["nodes"]}, g["edges"]


def checks_found(base_src, cand):
    return [(v["item"], v["found"]) for v in check_fixed_flow(fixed_flow(base_src)["items"], cand) if v["kind"] == "checks"]


# ------------------------------------------------------------------ 목록: kind checks
def test_list_has_one_checks_item_per_graph_and_no_rules_items():
    res = fixed_flow(RW)
    assert res["errors"] == []
    kinds = [i["kind"] for i in res["items"]]
    assert "rules" not in kinds and kinds.count("checks") == 1
    item = next(i for i in res["items"] if i["kind"] == "checks")
    assert item["id"] == "checks:run"
    classes = item["value"]["classes"]
    assert sorted(classes) == ["report"]          # note · default 갈래는 원래 검사가 없어 요구 0
    rep = classes["report"]
    assert rep["mode"] == "after_model"
    needs = [[s.split(" ")[1] for s in need] for need in rep["need"]]
    assert sorted(needs) == [["custom.report:grounded"], ["custom.report:grounded_2"]]
    assert item["basis"] == "graphs/run/nodes/check · graphs/run/nodes/check_2"


def test_unchanged_packs_keep_every_item():
    for src in (RW, MIN, TASK10):
        assert check_fixed_flow(fixed_flow(src)["items"], src) == [], src


# ------------------------------------------------------------------ T1 끝 기준 1: 다시 쓰기 고리 없앰 → 통과
def test_t1_rewrite_loop_removed_keeps_the_fixed_flow(tmp_path):
    b, cand = bridged(tmp_path, ["rewrite", "check_2"])
    assert b["exit"] == "finish_1"                 # ⓐ finish(읽는 text_2 · check_2 없음)는 흐름에 떨어지고 ⓑ check의 통과 쪽
    assert [t["exit"] for t in b["tried"]] == ["finish", "finish_1"] and b["tried"][0]["ok"] is False
    assert b["pruned"] == ["finish"]              # 함수 node지만 들어오는 길이 없어져 같이 빠짐
    assert check_fixed_flow(fixed_flow(RW)["items"], cand) == []
    nodes, edges = graph_of(cand)
    assert not {"rewrite", "check_2", "finish"} & set(nodes) and nodes["check"]["params"]["on_fail"] == "finish_1"
    assert ["check", "finish_1"] in edges and ["finish_1", "END"] in edges


# ------------------------------------------------------------------ T2 끝 기준 2: 마지막 검사 없앰 · 검사 느슨하게 → 어김
def test_t2a_first_check_removed_is_a_violation(tmp_path):
    """Hand-made (flow keeps a patch from making it: finish_1 reads check): write goes straight to finish_1."""
    cand = edited(tmp_path, "      - [write, check]\n      - [check, finish_1]\n      - [rewrite, check_2]\n      - [check_2, finish]\n"
                            "      - [finish, END]\n", "      - [write, finish_1]\n")
    text = (cand / "pack.yaml").read_text(encoding="utf-8")
    start = text.index("      - id: check\n")
    text = text[:start] + text[text.index("      - {id: finish_1,"):]
    text = text.replace("reads: [request, text, check], writes: [result]}", "reads: [request, text], writes: [result]}")
    (cand / "pack.yaml").write_text(text, encoding="utf-8")
    found = checks_found(RW, cand)
    assert len(found) == 1 and found[0][0] == "checks:run"
    assert "report 길 write → finish_1" in found[0][1] and "모델 node write 뒤 검사 없이 출력" in found[0][1]


@pytest.mark.parametrize("rel, old, new", [
    ("custom/report.py", 'return False, "empty"', 'return True, None'),                                      # 검사 함수가 부르는 같은 파일 함수
    ("custom/report.py", "    return _result(*judge_text((text or {}).get(\"text\"), basis(request)))",
     "    return _result(True, None)"),                                                                       # 검사 함수 몸
    ("custom/report_grounding.py", "return set(_WORD.findall(draft.text)) <= g.words", "return True"),     # 모듈 별칭 속성 rg.is_grounded
    ("custom/report_grounding.py", '_WORD = re.compile(r"[가-힣A-Za-z0-9.]+")', '_WORD = re.compile(r"x")'),  # rg 안 상수(정규식)
    ("custom/llm_base.py", "    text: str", "    text: str = \"\""),                                      # from custom.llm_base import Draft
])
def test_t2b_loosened_check_code_is_a_violation(tmp_path, rel, old, new):
    cand = edited(tmp_path, old, new, rel)
    found = checks_found(RW, cand)
    assert len(found) == 1 and "report 길" in found[0][1] and "custom.report:grounded" in found[0][1]
    assert "몸이 다름" in found[0][1]


def test_t2b_rule_removed_or_basic_rule_widened_is_a_violation(tmp_path):
    cand = edited(tmp_path, '            - custom: "custom.report:grounded"\n          on_fail: rewrite',
                  '            - {range: {path: state.request.kind, min: 0}}\n          on_fail: rewrite')
    found = checks_found(RW, cand)
    assert len(found) == 1 and "규칙 custom.report:grounded 빠짐" in found[0][1]
    base = edited(tmp_path, '            - custom: "custom.report:grounded"\n          on_fail: rewrite',
                  '            - custom: "custom.report:grounded"\n            - {range: {path: state.request.kind, min: 1, max: 2}}\n'
                  '          on_fail: rewrite', name="base")
    wide = edited(tmp_path, '            - custom: "custom.report:grounded"\n          on_fail: rewrite',
                  '            - custom: "custom.report:grounded"\n            - {range: {path: state.request.kind, min: 0, max: 9}}\n'
                  '          on_fail: rewrite', name="wide")
    found = checks_found(base, wide)
    assert len(found) == 1 and "규칙 range 빠짐" in found[0][1]   # 값 비교 0(Q-2): 넓힌 기본 규칙 = 같지 않음


def test_t2b_module_alias_passed_as_a_value_counts_the_whole_module(tmp_path):
    base = edited(tmp_path, "def judge_text(text, g):\n", "def judge_text(text, g, m=rg):\n", rel="custom/report.py", name="base")
    base_text = (base / "custom/report.py").read_text(encoding="utf-8")
    (base / "custom/report.py").write_text(base_text.replace("if rg.is_grounded(Draft(text), g):", "if m.is_grounded(Draft(text), g):"),
                                           encoding="utf-8")
    cand = edited(tmp_path, "def unused_helper(x):\n    return x * 2", "def unused_helper(x):\n    return x * 3",
                  rel="custom/report_grounding.py", src=base)
    found = checks_found(base, cand)
    assert len(found) == 1 and "몸이 다름" in found[0][1]   # 따라갈 수 없는 쓰임 → 모듈 파일 전체(엄한 쪽)


def test_t2c_check_moved_before_the_model_node_is_a_violation(tmp_path):
    cand = edited(tmp_path, "      - [rewrite, check_2]\n      - [check_2, finish]\n",
                  "      - [rewrite, finish]\n")
    cand_text = (cand / "pack.yaml").read_text(encoding="utf-8")
    start = cand_text.index("      - id: check_2")
    end = cand_text.index("      - {id: finish,")
    cand_text = cand_text[:start] + cand_text[end:]
    cand_text = cand_text.replace("reads: [request, text, check, text_2, check_2], writes: [result]",
                                  "reads: [request, text, check, text_2], writes: [result]")
    (cand / "pack.yaml").write_text(cand_text, encoding="utf-8")
    found = checks_found(RW, cand)
    assert len(found) == 1 and "report 길 write → check → rewrite → finish" in found[0][1]
    assert "모델 node rewrite 뒤 검사 없이 출력" in found[0][1]


# ------------------------------------------------------------------ T2d 지킴: 같은 파일 다른 함수 · 주석 · 설명 글만 → 어김 0
@pytest.mark.parametrize("rel, old, new", [
    ("custom/report_grounding.py", "def unused_helper(x):\n    return x * 2", "def unused_helper(x):\n    return x * 3"),
    ("custom/report.py", 'return {"result": {"text": request["draft"], "fallback": True}}',
     'return {"result": {"text": request["draft"] + "", "fallback": True}}'),                               # finish만
    ("custom/report.py", "def judge_text(text, g):\n", "def judge_text(text, g):  # 주석\n"),
    ("custom/report.py", "def basis(request):\n", "def basis(request):\n    \"\"\"설명 글.\"\"\"\n"),
])
def test_t2d_other_function_comment_or_docstring_only_keeps_the_item(tmp_path, rel, old, new):
    cand = edited(tmp_path, old, new, rel)
    assert checks_found(RW, cand) == []


# ------------------------------------------------------------------ T3 끝 기준 3: 갈래 안 단계를 빼고 다시 이어 돎
class Chat:
    """가짜 provider: 프롬프트 첫 낱말로 node를 알아보고 Text 하나를 돌려줌. fail=True면 첫 쓰기에 초안에 없는 낱말을 넣음."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None, **kw):
        prompt = messages[-1]["content"]
        draft = prompt.split(": ", 1)[1].split(" / ")[0].strip()
        self.calls.append(prompt.split(":")[0])
        text = draft + (" 바깥말" if self.fail and prompt.startswith("초안을 다듬는다") else "")
        return json.dumps({"text": text}, ensure_ascii=False), {"prompt_tokens": 1, "completion_tokens": 1}


class Sink:
    def __init__(self):
        self.events, self.payloads = [], []

    def put_payload(self, value, *, role, retention_class):
        self.payloads.append(value)
        return {"ref": "sha256:" + f"{len(self.payloads):064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


def run(tmp_path, folder, request, chat, review=None):
    work = tmp_path / "run"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(folder, work)
    text = (work / "pack.yaml").read_text(encoding="utf-8").replace("min_runtime_version: 0.10.0",
                                                                      f"min_runtime_version: {packrt.__version__}")
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    roots = tmp_path / "releases"
    roots.mkdir(exist_ok=True)
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: run\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n',
        encoding="utf-8")
    sink = Sink()
    res = Runtime(work, provider=chat).run(request, sink=sink, review=review)
    ran = [e["node_id"] for e in sink.events if e["type"] == "node_ended"]
    return res, ran


def test_t3a_router_case_step_removed_is_joined_again_and_runs(tmp_path):
    b, cand = bridged(tmp_path, ["prep_note"])
    assert b["exit"] == "write_note" and b["pruned"] == []
    nodes, _ = graph_of(cand)
    assert nodes["route_request"]["params"]["cases"] == {"report": "write", "note": "write_note"}
    assert check_fixed_flow(fixed_flow(RW)["items"], cand) == []
    res, ran = run(tmp_path, cand, {"kind": "note", "draft": "냉각 팬 열화"}, Chat())
    assert res["status"] == "succeeded" and json.loads(res["output"]) == {"text": "냉각 팬 열화", "fallback": False}
    assert "prep_note" not in ran and "note_out" in ran


def test_t3b_join_source_removed_is_joined_again_and_runs(tmp_path):
    b, cand = bridged(tmp_path, ["tag_note"])
    assert b["exit"] == "note_out"   # join 원천 자리에 앞 node(write_note)가 들어감 — 단순 연결을 더하지 않음(두 번 돌지 않게)
    _, edges = graph_of(cand)
    assert {"join": ["write_note", "mark_note"], "to": "note_out"} in edges and ["write_note", "note_out"] not in edges
    res, ran = run(tmp_path, cand, {"kind": "note", "draft": "냉각 팬 열화"}, Chat())
    assert res["status"] == "succeeded" and "tag_note" not in ran and ran.count("note_out") == 1


def test_t3b_on_fail_branch_removed_runs_both_ways(tmp_path):
    _, cand = bridged(tmp_path, ["rewrite", "check_2"])
    res, ran = run(tmp_path, cand, {"kind": "report", "draft": "냉각 팬 열화"}, Chat())
    assert res["status"] == "succeeded" and res["output"] == {"text": "냉각 팬 열화", "fallback": False} and "finish_1" in ran
    res, ran = run(tmp_path, cand, {"kind": "report", "draft": "냉각 팬 열화"}, Chat(fail=True))
    assert res["status"] == "succeeded" and res["output"] == {"text": "냉각 팬 열화", "fallback": True}   # 떨어지면 원문
    assert "rewrite" not in ran and ran[-1] == "finish_1"


def test_t3_original_still_runs_the_rewrite_way(tmp_path):
    res, ran = run(tmp_path, RW, {"kind": "report", "draft": "냉각 팬 열화"}, Chat(fail=True))
    assert res["status"] == "succeeded" and "rewrite" in ran and ran[-1] == "finish"


# ------------------------------------------------------------------ T3c 지킴: 출구 고르기 · 거절
def test_t3c_branch_node_needs_an_exit_and_a_reachable_function_node_stays():
    b = bridge_ops(RW, "run", ["check"])
    assert b["after"]["ok"] is False and b["ops"] == [] and "갈래" in b["after"]["errors"][0]["message"]
    b = bridge_ops(RW, "run", ["finish_1"])
    assert b["after"]["ok"] is False and "함수 node 'finish_1'" in b["after"]["errors"][0]["message"]
    b = bridge_ops(RW, "run", ["nope"])
    assert b["after"]["ok"] is False and "nope" in b["after"]["errors"][0]["message"]
    res = apply_patch(RW, [{"op": "remove_node", "graph": "run", "node": "finish_1"}])
    assert not res.ok   # 닿는 함수 node 지우기는 지금처럼 거절


def test_t3c_given_exit_is_used_and_a_flow_problem_is_reported():
    b = bridge_ops(RW, "run", ["prep_note"], exit="write_note")
    assert b["exit"] == "write_note" and b["tried"] == [{"exit": "write_note", "ok": True, "errors": []}]
    b = bridge_ops(RW, "run", ["check"], exit="finish_1")   # finish_1 reads check: flow F3 stops it
    assert b["after"]["ok"] is False and b["ops"] == [] and b["exit"] is None
    assert [t["exit"] for t in b["tried"]] == ["finish_1"] and "함수 node 'finish_1'" in b["after"]["errors"][0]["message"]


def test_remove_unreachable_alone_keeps_a_whole_pack():
    res = apply_patch(RW, [{"op": "remove_unreachable", "graph": "run"}])
    assert res.ok and res.changed_files == []


# ------------------------------------------------------------------ T4 지킴: 옛 rules 항목 · 데이터 node 같이 빠짐
def test_t4_old_rules_items_keep_their_old_meaning(tmp_path):
    old = [{"id": "rules:1", "kind": "rules", "what": "결과 검사 규칙 1(check)이 남음", "basis": "graphs/run/nodes/check",
            "value": {"rules_sha": "sha256:" + "0" * 64, "graphs": ["run"]}}]
    v = check_fixed_flow(old, RW)
    assert [(x["item"], x["found"]) for x in v] == [("rules:1", "같은 규칙을 쓰는 결과 검사 node가 없음")]


def test_t4_store_reading_node_taken_out_is_caught(tmp_path):
    b = bridge_ops(MIN, "run", ["find_notes"])
    assert b["after"]["ok"] is True and b["exit"] == "evidence_check"
    cand = patched(tmp_path, b["ops"], src=MIN)
    assert [v["item"] for v in check_fixed_flow(fixed_flow(MIN)["items"], cand)] == ["stores:notes"]



# ------------------------------------------------------------------ T3d · T3e (오케 답 143 △): loop · 사람 관문 routes 다시 잇기
LOOP_PACK = """# 시험용(D-462 T3d): 쓰기 → 다듬기 → 검사, 떨어지면 고치기 → 다듬기로 돌아감(반복 한도 2) · 통과면 finish_1.
ir_version: 1
runtime_contract: {min_runtime_version: 0.10.0}
pack: {id: ff_loop, version: V1, title: "반복 다시 잇기 시험"}
env: {required: [OPENAI_API_KEY], optional: [OPENAI_BASE_URL]}
schemas:
  Request: schemas/request.json
  Text: schemas/text.json
  Result: schemas/result.json
graphs:
  run:
    state:
      request: Request
      text: Text
      check: dict
      result: Result
    nodes:
      - {id: write, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/write.md, output: Text, temperature: 0}, reads: [request], writes: [text]}
      - {id: polish, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/polish.md, output: Text, temperature: 0}, reads: [request, text], writes: [text]}
      - id: check
        kind: validate@2
        params:
          rules:
            - custom: "custom.report:grounded"
          on_fail: fix
        reads: [request, text]
        writes: [check]
      - {id: fix, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/fix.md, output: Text, temperature: 0}, reads: [request, text, check], writes: [text]}
      - {id: finish_1, kind: custom_code@3, params: {fn: "custom.report:finish"}, capabilities: {network: none, side_effect: none}, reads: [request, text, check], writes: [result]}
    edges:
      - [START, write]
      - [write, polish]
      - [polish, check]
      - [check, finish_1]
      - {from: fix, to: polish, max_iterations: 2}
      - [finish_1, END]
entrypoints:
  - {graph: run, input: Request, output: Result}
trace: {contract: 1}
"""


@pytest.fixture()
def loop_pack(tmp_path):
    folder = copy_pack(RW, tmp_path, "loop")
    (folder / "pack.yaml").write_text(LOOP_PACK, encoding="utf-8")
    (folder / "prompts" / "polish.md").write_text("다듬기: {state.request.draft} / {state.text.text}\n", encoding="utf-8")
    (folder / "prompts" / "fix.md").write_text("고치기: {state.request.draft} / {state.text.text} / {state.check.reason}\n", encoding="utf-8")
    res = load_pack(folder)
    assert res.ok, res.errors
    return folder


def test_t3d_loop_target_taken_out_moves_the_loop_and_runs(tmp_path, loop_pack):
    b = bridge_ops(loop_pack, "run", ["polish"])
    assert b["after"]["ok"], (b["tried"], b["after"])
    assert b["exit"] == "check" and b["pruned"] == []
    cand = patched(tmp_path, b["ops"], src=loop_pack)
    _, edges = graph_of(cand)
    assert {"from": "fix", "to": "check", "max_iterations": 2} in edges and ["write", "check"] in edges
    assert check_fixed_flow(fixed_flow(loop_pack)["items"], cand) == []
    res, ran = run(tmp_path, cand, {"kind": "report", "draft": "냉각 팬 열화"}, Chat(fail=True))   # 첫 글이 떨어짐 → 고치기 → 다시 검사
    assert res["status"] == "succeeded" and res["output"] == {"text": "냉각 팬 열화", "fallback": False}
    assert ran == ["write", "check", "fix", "check", "finish_1"]


def test_t3d_loop_start_taken_out_alone_is_refused(loop_pack):
    """Taking out fix alone would lose the loop's bound (check → on_fail → polish → check with no max_iterations)."""
    b = bridge_ops(loop_pack, "run", ["fix"])
    assert b["after"]["ok"] is False and b["ops"] == [] and b["tried"] == []
    assert "반복" in b["after"]["errors"][0]["message"] and "fix" in b["after"]["errors"][0]["message"]


def human_pack(tmp_path):
    folder = edited(tmp_path, "      - [check, finish_1]\n", "      - [check, review]\n      - [tidy, finish_1]\n", name="human")
    text = (folder / "pack.yaml").read_text(encoding="utf-8")
    text = text.replace("      result: Result\n", "      review: dict\n      result: Result\n")
    text = text.replace("      - {id: prep_note,",
                        "      - id: review\n        kind: human_review@1\n"
                        "        params: {subject: text, actions: [approve, edit, reject], routes: {approve: finish_1, edit: tidy, reject: END}}\n"
                        "        reads: [text, check]\n        writes: [review]\n"
                        "      - {id: tidy, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/tidy.md, output: Text, "
                        "temperature: 0}, reads: [request, text], writes: [text]}\n"
                        "      - {id: prep_note,")
    (folder / "pack.yaml").write_text(text, encoding="utf-8")
    (folder / "prompts" / "tidy.md").write_text("손보기: {state.request.draft} / {state.text.text}\n", encoding="utf-8")
    res = load_pack(folder)
    assert res.ok, res.errors
    return folder


def test_t3e_review_route_step_taken_out_is_joined_again_and_runs(tmp_path):
    base = human_pack(tmp_path)
    b = bridge_ops(base, "run", ["tidy"])
    assert b["after"]["ok"], (b["tried"], b["after"])
    assert b["exit"] == "finish_1" and b["pruned"] == []
    cand = patched(tmp_path, b["ops"], src=base)
    nodes, _ = graph_of(cand)
    assert nodes["review"]["params"]["routes"] == {"approve": "finish_1", "edit": "finish_1", "reject": "END"}
    work_res, ran = run(tmp_path, cand, {"kind": "report", "draft": "냉각 팬 열화"}, Chat(), review={"policy": "auto_approve"})
    assert work_res["status"] == "succeeded" and "review" in ran and ran[-1] == "finish_1"


def test_t3e_review_route_change_breaks_the_human_item(tmp_path):
    base = human_pack(tmp_path)
    cand = patched(tmp_path, bridge_ops(base, "run", ["tidy"])["ops"], src=base)
    v = check_fixed_flow(fixed_flow(base)["items"], cand)
    assert [(x["item"], x["found"]) for x in v] == [("human:review", "사람 관문 review의 설정이 run에서 다름")]   # 다른 항목은 지금 엄함 그대로


# ------------------------------------------------------------------ 0.13.1 (오케 답 146): 모델 출력이 닿지 않는 길은 검사 요구 밖
# task10-pack 2c74ac0 꼴: 첫 router(route_mode)의 두 갈래가 모두 '이상 없음' 기록 길(모델 0 · 검사 0)을 품는다. 0.13.0은 그 길
# 때문에 두 요청 종류 모두 요구를 잃어 결과 검사 항목이 0개였다(answer_check를 빼도 어김 0).
def _ml_split(tmp_path, extra=()):
    folder = copy_pack(TASK10, tmp_path, "mlsplit")
    pack = folder / "pack.yaml"
    text = pack.read_text(encoding="utf-8")
    nodes_at = "    nodes:\n      - {id: predict_failure,"
    routers = ("    nodes:\n"
               "      - {id: route_mode, kind: router@1, params: {by: state.event.source_mode, cases: {ml_predicted: route_prediction}, default: check_alarm}, reads: [event], writes: []}\n"
               "      - {id: route_prediction, kind: router@1, params: {by: state.event.predicted, cases: {NONE: record_no_issue_ml}, default: predict_failure}, reads: [event], writes: []}\n"
               "      - {id: check_alarm, kind: router@1, params: {by: state.event.alarm, cases: {none: record_no_issue}, default: predict_failure}, reads: [event], writes: []}\n"
               "      - {id: record_no_issue, kind: render@1, params: {template: templates/no_issue.md, format: md, to: report}, reads: [event], writes: [report]}\n"
               "      - {id: record_no_issue_ml, kind: render@1, params: {template: templates/no_issue.md, format: md, to: report}, reads: [event], writes: [report]}\n"
               "      - {id: predict_failure,")
    for old, new in ((nodes_at, routers),
                     ("      - [START, predict_failure]\n", "      - [START, route_mode]\n      - [record_no_issue, END]\n      - [record_no_issue_ml, END]\n"),
                     *extra):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    pack.write_text(text, encoding="utf-8")
    (folder / "templates" / "no_issue.md").write_text("이상 없음: {state.event.summary}\n", encoding="utf-8")
    res = load_pack(folder)
    assert res.ok, res.errors
    return folder


def test_t5a_no_model_ways_do_not_drop_the_check_requirement(tmp_path):
    base = _ml_split(tmp_path)
    items = fixed_flow(base)["items"]
    item = next((i for i in items if i["kind"] == "checks"), None)
    assert item is not None, [i["id"] for i in items]
    assert item["id"] == "checks:respond" and item["basis"] == "graphs/respond/nodes/answer_check"
    assert sorted(item["value"]["classes"]) == ["default", "ml_predicted"]
    for cls in item["value"]["classes"].values():
        assert cls["mode"] == "after_model"
        assert [sorted(s.split(" ", 1)[1] for s in need) for need in cls["need"]] == [
            ['{"evidence_ids":"answer"}', '{"schema":"GroundedResponse"}', '{"unsafe_assertion":"answer"}']]
    assert "rules" not in [i["kind"] for i in items]


def test_t5b_answer_check_loosened_is_a_violation(tmp_path):
    base = _ml_split(tmp_path)
    (tmp_path / "c").mkdir()
    cand = _ml_split(tmp_path / "c", extra=[(", {unsafe_assertion: answer}]}", "]}")])
    found = checks_found(base, cand)
    assert len(found) == 1 and found[0][0] == "checks:respond"
    assert "원래 마지막 검사와 같거나 더 엄한 검사가 없음" in found[0][1] and "규칙 unsafe_assertion 빠짐" in found[0][1]


def test_t5c_no_issue_way_and_first_check_stay_free(tmp_path):
    base = _ml_split(tmp_path)
    (tmp_path / "c").mkdir()
    cand = _ml_split(tmp_path / "c", extra=[("require_source_types: [manual]}}", "require_source_types: []}}")])
    (cand / "templates" / "no_issue.md").write_text("이상 없음(바뀐 글)\n", encoding="utf-8")
    assert checks_found(base, cand) == []
    assert check_fixed_flow(fixed_flow(base)["items"], base) == []


def test_t5d_a_no_issue_way_that_reads_model_output_needs_the_check(tmp_path):
    base = _ml_split(tmp_path)
    (tmp_path / "c").mkdir()
    cand = _ml_split(tmp_path / "c", extra=[(
        "      - {id: record_no_issue, kind: render@1, params: {template: templates/no_issue.md, format: md, to: report}, reads: [event], writes: [report]}\n",
        "      - {id: record_no_issue, kind: render@1, params: {template: templates/no_issue.md, format: md, to: report}, reads: [event, answer], writes: [report]}\n")])
    found = checks_found(base, cand)
    assert len(found) == 1 and "record_no_issue" in found[0][1] and "검사 없이 출력" in found[0][1]


SKIP_PACK = """# 시험용(0.13.1 T5e): 쓰기(모델) 뒤 갈래 하나가 검사를 건너뛰고 출력 — 원래부터 지킬 요구가 없는 그래프.
ir_version: 1
runtime_contract: {min_runtime_version: 0.10.0}
pack: {id: ff_skip, version: V1, title: "검사 건너뛰는 길 시험"}
env: {required: [OPENAI_API_KEY], optional: [OPENAI_BASE_URL]}
schemas:
  Request: schemas/request.json
  Text: schemas/text.json
  Result: schemas/result.json
graphs:
  run:
    state:
      request: Request
      text: Text
      check: dict
      result: Result
    nodes:
      - {id: write, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/write.md, output: Text, temperature: 0}, reads: [request], writes: [text]}
      - {id: route_after, kind: router@1, params: {by: state.request.kind, cases: {strict: check}, default: finish_1}, reads: [request], writes: []}
      - id: check
        kind: validate@2
        params:
          rules:
            - custom: "custom.report:grounded"
            - {schema: Text}
          on_fail: finish_1
        reads: [request, text]
        writes: [check]
      - {id: finish_1, kind: custom_code@3, params: {fn: "custom.report:finish"}, capabilities: {network: none, side_effect: none}, reads: [request, text, check], writes: [result]}
    edges:
      - [START, write]
      - [write, route_after]
      - [check, finish_1]
      - [finish_1, END]
entrypoints:
  - {graph: run, input: Request, output: Result}
trace: {contract: 1}
"""


def test_t5e_no_requirement_left_keeps_the_old_rules_items(tmp_path):
    # 모델 길 하나가 원래 검사 없이 출력 → 요구 0. 0.13.0은 이때 결과 검사 항목이 하나도 없었다 → 옛 rules 항목(엄한 쪽)을 둔다
    base = copy_pack(RW, tmp_path, "skip")
    (base / "pack.yaml").write_text(SKIP_PACK, encoding="utf-8")
    assert load_pack(base).ok, load_pack(base).errors
    items = fixed_flow(base)["items"]
    assert "checks" not in [i["kind"] for i in items]
    rules = [i for i in items if i["kind"] == "rules"]
    assert [(i["id"], i["basis"]) for i in rules] == [("rules:run:1", "graphs/run/nodes/check")]
    cand = edited(tmp_path, "            - {schema: Text}\n", "", src=base, name="loose")
    assert [v["item"] for v in check_fixed_flow(items, cand)] == ["rules:run:1"]
    assert check_fixed_flow(items, base) == []
