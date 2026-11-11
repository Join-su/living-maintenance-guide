"""약속 v9(contracts/v9 = v8 + judge 사건 switch_check · exception_start · kind exception_similar · exception_split · D-556 · 덧 7) 시험.
요청: kind 둘 · 사건 칸 from_section · items · text 칸만 더함 / 결과: exception_similar · exception_split 칸만 더함 · 예시 = v8 바이트 + 새 셋 짝(Pack 함수 실제 출력) · v1 ~ v8은 그대로."""
import copy
import json
import sys
import unittest
from pathlib import Path

import jsonschema

from contract_cases import CONTRACTS

sys.path.insert(0, str(CONTRACTS.parent / "pack"))
from custom import judge  # noqa: E402

V8, V9 = CONTRACTS / "v8", CONTRACTS / "v9"
NEW = {f"{n}.{s}.json" for n in ("judge-switch", "judge-exception-start", "exception-similar", "exception-split") for s in ("request", "result")}


def _load(v, name):
    return json.loads((v / name).read_text(encoding="utf-8"))


def _validator(v, name):
    s = _load(v, name)
    jsonschema.Draft202012Validator.check_schema(s)
    return jsonschema.Draft202012Validator(s)


class Shape(unittest.TestCase):
    def test_only_new_fields(self):
        old, new = _load(V8, "run_request.json"), _load(V9, "run_request.json")
        self.assertEqual(new["$id"], "c1a15-pack/contracts/v9/run_request.json")
        self.assertEqual(set(new["properties"]) - set(old["properties"]), {"items", "text"})
        self.assertEqual(new["properties"]["kind"]["enum"], [*old["properties"]["kind"]["enum"], "exception_similar", "exception_split"])
        self.assertEqual(set(new["$defs"]["event"]["properties"]) - set(old["$defs"]["event"]["properties"]), {"from_section"})
        self.assertEqual(len(new["allOf"]), len(old["allOf"]) + 2)
        oldr, newr = _load(V8, "run_result.json"), _load(V9, "run_result.json")
        self.assertEqual(set(newr["properties"]) - set(oldr["properties"]), {"exception_similar", "exception_split"})
        self.assertEqual({k: v for k, v in newr["properties"].items() if k not in ("exception_similar", "exception_split", "exception_guide")}, {k: v for k, v in oldr["properties"].items() if k != "exception_guide"})
        g8, g9 = oldr["properties"]["exception_guide"], newr["properties"]["exception_guide"]   # D-556 덧 12: v8과 다른 곳 = check.how · criteria 빈 글 허용 둘
        self.assertEqual(g9["properties"]["check"]["properties"]["criteria"]["type"], "string")
        self.assertNotIn("minLength", g9["properties"]["check"]["properties"]["criteria"])
        self.assertNotIn("minLength", g9["properties"]["check"]["properties"]["how"])
        drop = lambda g: {**g, "properties": {**g["properties"], "check": {**g["properties"]["check"], "properties": {k: v for k, v in g["properties"]["check"]["properties"].items() if k not in ("how", "criteria")}}}}
        self.assertEqual(drop(g9), drop(g8))

    def test_examples(self):
        old = {p.name for p in (V8 / "examples").glob("*.json")}
        new = {p.name for p in (V9 / "examples").glob("*.json")}
        self.assertEqual(new, old | NEW)
        req, res = _validator(V9, "run_request.json"), _validator(V9, "run_result.json")
        for n in sorted(new):
            v = req if n.endswith(".request.json") else res
            self.assertEqual([e.message for e in v.iter_errors(_load(V9 / "examples", n))], [], n)
            if n in old:
                self.assertEqual((V9 / "examples" / n).read_bytes(), (V8 / "examples" / n).read_bytes(), n)

    def test_new_examples_are_pack_output(self):   # 실제 출력 꼴: 새 결과 예시 = 그 요청을 Pack 함수에 넣은 출력
        for n in ("judge-switch", "judge-exception-start"):
            self.assertEqual(judge.judge(_load(V9 / "examples", f"{n}.request.json"))["result"], _load(V9 / "examples", f"{n}.result.json"), n)
        # D-566: exception_split · exception_similar는 v10부터 Pack 출력 꼴이 바뀜(예외 하나) — v9 예시(짝 꼴)는 v9 schema에 맞는지만 보고, Pack 출력 견줌은 test_contract_v10
        self.assertEqual(len(_load(V9 / "examples", "exception-split.result.json")["exception_split"]["items"]), 2)
        sw = _load(V9 / "examples", "judge-switch.result.json")["decision"]
        self.assertEqual((sw["kind"], sw["section"], sw["switched"]), ("instruct", "4.5", {"from": "4.3", "to": "4.5"}))
        st = _load(V9 / "examples", "judge-exception-start.result.json")["decision"]
        self.assertEqual((st["kind"], st["section"], st["reason"]), ("call", "EX-NEW", "exception_progress"))


class Rejects(unittest.TestCase):   # 실패 쪽: v8은 새 사건 칸 · 새 kind를 모름 · kind 칸 섞임 거절 · 비슷한 것 결과 꼴 밖
    REQ, RES = _validator(V9, "run_request.json"), _validator(V9, "run_result.json")

    def test_v8_rejects_new(self):
        r8 = _validator(V8, "run_request.json")
        self.assertFalse(r8.is_valid(_load(V9 / "examples", "judge-switch.request.json")))   # from_section 모름
        self.assertFalse(r8.is_valid(_load(V9 / "examples", "exception-similar.request.json")))
        self.assertFalse(_validator(V8, "run_result.json").is_valid(_load(V9 / "examples", "exception-similar.result.json")))
        self.assertFalse(r8.is_valid(_load(V9 / "examples", "exception-split.request.json")))
        self.assertFalse(_validator(V8, "run_result.json").is_valid(_load(V9 / "examples", "exception-split.result.json")))

    def test_bad(self):
        s = _load(V9 / "examples", "exception-similar.request.json")
        self.assertFalse(self.REQ.is_valid({k: v for k, v in s.items() if k != "items"}))
        self.assertFalse(self.REQ.is_valid({**s, "events": []}))
        self.assertFalse(self.REQ.is_valid({**s, "items": []}))
        self.assertFalse(self.REQ.is_valid({**_load(V9 / "examples", "judge.request.json"), "items": s["items"]}))
        x = copy.deepcopy(_load(V9 / "examples", "exception-similar.result.json"))
        x["exception_similar"][0]["matches"][0]["kind"] = "이력"
        self.assertFalse(self.RES.is_valid(x))
        self.assertFalse(self.RES.is_valid({**x, "decision": {"kind": "closed"}}))
        sp = _load(V9 / "examples", "exception-split.request.json")   # exception_split: text 필수 · 빈 글 · items 섞임 · 다른 kind에 text 거절
        self.assertFalse(self.REQ.is_valid({k: v for k, v in sp.items() if k != "text"}))
        self.assertFalse(self.REQ.is_valid({**sp, "text": ""}))
        self.assertFalse(self.REQ.is_valid({**sp, "items": s["items"]}))
        self.assertFalse(self.REQ.is_valid({**s, "text": sp["text"]}))
        y = _load(V9 / "examples", "exception-split.result.json")
        self.assertTrue(self.RES.is_valid({"exception_split": {"items": [], "reason": "not_split"}}))
        self.assertFalse(self.RES.is_valid({"exception_split": {"items": [], "reason": "timeout"}}))
        self.assertFalse(self.RES.is_valid({**y, **_load(V9 / "examples", "exception-similar.result.json")}))


if __name__ == "__main__":
    unittest.main()
