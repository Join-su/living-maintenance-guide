"""약속 v10(contracts/v10 = v9 + D-566 예외 하나 = 주제 + 점검 목록 + 조치 목록) 시험.
요청: exception_similar `items` → `exception` 하나 · 사건 `step` 칸 / 결과: exception_split `{exception | null, reason}` · exception_similar `{matches}` · exception_guide `checks` · `actions` · `topic`.
예시 = v9 바이트(바뀌지 않은 것) + 새 · 바뀐 짝(Pack 함수 실제 출력 · 걸음 길 예시는 아래 LISTS 그래프 덧댐으로) · v1 ~ v9는 그대로.
꼴 정본 = orchestration agents/c1a15 handoff/d566/contract-v10.md."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import jsonschema

from contract_cases import CONTRACTS

sys.path.insert(0, str(CONTRACTS.parent / "pack"))
from custom import graph_query as G, judge, similar  # noqa: E402
from packrt.run.engine import Runtime  # noqa: E402
from test_pack import FakeProvider  # noqa: E402

V9, V10 = CONTRACTS / "v9", CONTRACTS / "v10"
CHANGED = {"exception-split.result.json", "exception-similar.request.json", "exception-similar.result.json"}
NEW = {"start-exception-lists.result.json", "judge-exception-walk.request.json", "judge-exception-walk.result.json"}
SID = "EX-R-9001"


def _load(v, name):
    return json.loads((v / name).read_text(encoding="utf-8"))


def _validator(v, name):
    s = _load(v, name)
    jsonschema.Draft202012Validator.check_schema(s)
    return jsonschema.Draft202012Validator(s)


def lists_graph():
    """걸음 길 예시 그래프 = 지금 그래프 + 점검 둘 · 조치 둘인 예외 절 하나(EX-R-9001) · 안내 관계 하나(냉각 · 온도 차 ≤ 8.2)."""
    rel = copy.deepcopy(G.release())
    rel["sections"].append({"no": SID, "app_no": SID, "kind": "예외", "grade": "C", "name": "필터 덮개 고정 나사 확인", "topic": "필터 덮개 나사",
                            "guide": "손으로 돌려 봄", "steps": ["손으로 돌려 봄", "눈금을 봄"], "criteria": "모두 체결 = 정상", "group": "온도 차 저하", "minutes": 5, "checklist": [], "records": ["R-9001"],
                            "checks": [{"what": "필터 덮개 고정 나사", "how": "손으로 돌려 봄", "criteria": "모두 체결 = 정상", "minutes": 5},
                                       {"what": "냉각수 수위", "how": "눈금을 봄", "criteria": "", "minutes": 3}],
                            "actions": [{"what": "풀린 나사 다시 조임", "parts": [], "minutes": 5}, {"what": "냉각수 보충", "parts": ["냉각수"], "minutes": 10}]})
    rel["exception_relations"] = [{"id": SID, "step": SID, "mode": "안내", "grade": "C", "cause": "CL-1", "cause_name": "냉각 필터 막힘", "records": ["R-9001"], "n": 1,
                                   "when": {"group": "온도 차 저하", "sensors": [["temp_diff_k", "<=", 8.2]]}}]
    return rel


class _Graph:
    def __init__(self, rel):
        self.rel, self.p = rel, None

    def __enter__(self):
        real, cur = G._read, str(G.CURRENT.resolve())
        judge.manual.cache_clear()
        G.release.cache_clear()
        self.p = mock.patch.object(G, "_read", lambda p: self.rel if p == cur else real(p))
        self.p.start()

    def __exit__(self, *a):
        self.p.stop()
        judge.manual.cache_clear()
        G.release.cache_clear()


def outputs():
    """새 · 바뀐 예시 = Pack 함수 실제 출력(가짜 provider · 그래프 파일만)."""
    split_req = _load(V10 / "examples", "exception-split.request.json")
    split = Runtime(CONTRACTS.parent / "pack", provider=FakeProvider()).run(split_req)["output"]
    sim_req = {"kind": "exception_similar", "session_id": split_req["session_id"], "group": split_req["group"], "exception": split["exception_split"]["exception"]}
    sim = similar.find(sim_req)["result"]
    ctx = _load(V10 / "examples", "start-context.request.json")
    with _Graph(lists_graph()):
        start = judge.start(ctx, G.query(ctx, judge.history_query(ctx)["history_query"])["history_hits"])["result"]
        ev = [{"type": "lock"}, {"type": "precheck_done"}, {"type": "call_answer", "answer": "approved", "section": SID},
              {"type": "result", "section": SID, "step": "c1", "value": "정상"}]
        walk_req = {"kind": "judge", "session_id": "S-12", "group": "CL", "similar": start["similar"], "grades": ctx["grades"], "minutes": ctx["minutes"],
                    "events": [{"seq": i + 1, **e} for i, e in enumerate(ev)], "exception_relation": SID}
        walk = judge.judge(walk_req)["result"]
    return {"exception-split.result.json": split, "exception-similar.request.json": sim_req, "exception-similar.result.json": sim,
            "start-exception-lists.result.json": start, "judge-exception-walk.request.json": walk_req, "judge-exception-walk.result.json": walk}


class Shape(unittest.TestCase):
    def test_only_new_fields(self):
        old, new = _load(V9, "run_request.json"), _load(V10, "run_request.json")
        self.assertEqual(new["$id"], "c1a15-pack/contracts/v10/run_request.json")
        self.assertEqual((set(new["properties"]) - set(old["properties"]), set(old["properties"]) - set(new["properties"])), ({"exception", "recent_work"}, {"items"}))   # recent_work = D-570 덧(start 선택)
        self.assertEqual(new["properties"]["kind"]["enum"], old["properties"]["kind"]["enum"])
        self.assertEqual(set(new["$defs"]["event"]["properties"]) - set(old["$defs"]["event"]["properties"]), {"step"})
        oldr, newr = _load(V9, "run_result.json"), _load(V10, "run_result.json")
        self.assertEqual(set(newr["properties"]), set(oldr["properties"]))
        same = {k for k in newr["properties"] if k not in ("exception_split", "exception_similar", "exception_guide")}
        self.assertEqual({k: newr["properties"][k] for k in same}, {k: oldr["properties"][k] for k in same})
        g9, g10 = oldr["properties"]["exception_guide"]["properties"], newr["properties"]["exception_guide"]["properties"]
        self.assertEqual((set(g10) - set(g9), set(g9) - set(g10)), ({"topic", "checks", "actions", "matched"}, {"check", "action"}))   # matched = D-580 덧
        drop = lambda it: {**it, "properties": {k: v for k, v in it["properties"].items() if k != "instruction"}}   # D-642 C 선택 칸 instruction만 더함
        self.assertEqual(drop(g10["checks"]["items"]), g9["check"])   # 칸 꼴은 v9 하나와 같고 목록이 됨
        self.assertEqual(drop(g10["actions"]["items"]), g9["action"])
        self.assertEqual({k for k in g10["checks"]["items"]["properties"]} - set(g9["check"]["properties"]), {"instruction"})

    def test_examples(self):
        old = {p.name for p in (V9 / "examples").glob("*.json")}
        new = {p.name for p in (V10 / "examples").glob("*.json")}
        self.assertEqual(new, old | NEW)
        req, res = _validator(V10, "run_request.json"), _validator(V10, "run_result.json")
        for n in sorted(new):
            v = req if n.endswith(".request.json") else res
            self.assertEqual([e.message for e in v.iter_errors(_load(V10 / "examples", n))], [], n)
            if n in old and n not in CHANGED:
                self.assertEqual((V10 / "examples" / n).read_bytes(), (V9 / "examples" / n).read_bytes(), n)

    def test_examples_are_pack_output(self):   # 실제 출력 꼴: 새 · 바뀐 예시 = Pack 함수 출력
        out = outputs()
        for n, v in out.items():
            self.assertEqual(_load(V10 / "examples", n), v, n)
        e = out["exception-split.result.json"]["exception_split"]["exception"]
        self.assertEqual((len(e["checks"]), len(e["actions"]), e["topic"]), (2, 2, "필터 덮개 나사"))   # 점검 둘 · 조치 둘인 예외 하나
        self.assertEqual(set(out["exception-similar.result.json"]["exception_similar"]), {"matches"})
        g = out["start-exception-lists.result.json"]["exception_guide"]
        self.assertEqual((len(g["checks"]), len(g["actions"]), g["topic"]), (2, 2, "필터 덮개 나사"))
        w = out["judge-exception-walk.result.json"]["decision"]
        self.assertEqual((w["kind"], w["section"], w["step"], w["step_index"], w["steps_total"], w["check"]["what"]), ("instruct", SID, "c2", 2, 4, "냉각수 수위"))


class Rejects(unittest.TestCase):   # 실패 쪽: v9는 새 꼴을 모름 · v10은 옛 짝 꼴 · 한쪽 0 · 꼴 밖 step 거절
    REQ, RES = _validator(V10, "run_request.json"), _validator(V10, "run_result.json")

    def test_v9_rejects_new(self):
        r9, s9 = _validator(V9, "run_request.json"), _validator(V9, "run_result.json")
        self.assertFalse(r9.is_valid(_load(V10 / "examples", "exception-similar.request.json")))
        self.assertFalse(r9.is_valid(_load(V10 / "examples", "judge-exception-walk.request.json")))   # step 칸
        for n in ("exception-split.result.json", "exception-similar.result.json", "start-exception-lists.result.json"):
            self.assertFalse(s9.is_valid(_load(V10 / "examples", n)), n)

    def test_bad(self):
        self.assertFalse(self.REQ.is_valid(_load(V9 / "examples", "exception-similar.request.json")))   # v9 items 꼴
        self.assertFalse(self.RES.is_valid(_load(V9 / "examples", "exception-split.result.json")))
        s = _load(V10 / "examples", "exception-similar.request.json")
        self.assertFalse(self.REQ.is_valid({**s, "exception": {**s["exception"], "checks": []}}))
        self.assertFalse(self.REQ.is_valid({**s, "exception": {**s["exception"], "actions": []}}))
        self.assertFalse(self.REQ.is_valid({**_load(V10 / "examples", "judge.request.json"), "exception": s["exception"]}))
        sp = _load(V10 / "examples", "exception-split.result.json")
        self.assertTrue(self.RES.is_valid({"exception_split": {"exception": None, "reason": "not_split"}}))
        self.assertFalse(self.RES.is_valid({"exception_split": {**sp["exception_split"], "exception": {**sp["exception_split"]["exception"], "actions": []}}}))
        self.assertFalse(self.RES.is_valid({"exception_split": {**sp["exception_split"], "exception": {**sp["exception_split"]["exception"], "topic": "가" * 16}}}))
        w = _load(V10 / "examples", "judge-exception-walk.request.json")
        self.assertFalse(self.REQ.is_valid({**w, "events": [{**w["events"][-1], "step": "b1"}]}))


if __name__ == "__main__":
    unittest.main()
