"""약속 v8(contracts/v8 = v7 + 결과 선택 칸 exception_guide · judge 요청 선택 칸 exception_relation, D-545) 시험.
칸 둘만 더함 · 예시 = v7 바이트 + 예외 셋(Pack 함수 실제 출력) · v1 ~ v7은 그대로.
D-546 덧 36: report 요청 allowed_sections 꼴만 넓힘(매뉴얼 절 번호 + EX-… · 예시 report-exception)."""
import copy
import json
import unittest

import jsonschema

from contract_cases import CONTRACTS

V7, V8 = CONTRACTS / "v7", CONTRACTS / "v8"
NEW = {"start-exception.result.json", "judge-exception.request.json", "judge-exception.result.json", "report-exception.request.json"}
SECTION = "^[0-9]\\.[0-9](\\.[0-9])?$"   # v7 allowed_sections 꼴(매뉴얼 절 번호만)


def _load(v, name):
    return json.loads((v / name).read_text(encoding="utf-8"))


def _validator(v, name):
    s = _load(v, name)
    jsonschema.Draft202012Validator.check_schema(s)
    return jsonschema.Draft202012Validator(s)


def _strip(d, key):
    d = copy.deepcopy(d)
    del d["properties"][key]
    return {k: v for k, v in d.items() if k not in ("$id", "description")}


class Shape(unittest.TestCase):
    def test_one_field_each(self):
        for name, key in (("run_request.json", "exception_relation"), ("run_result.json", "exception_guide")):
            old, new = _load(V7, name), _load(V8, name)
            self.assertEqual(new["$id"], f"c1a15-pack/contracts/v8/{name}")
            self.assertEqual(set(new["properties"]) - set(old["properties"]), {key}, name)
            if name == "run_request.json":   # 넓힌 칸 하나(allowed_sections 꼴) — 나머지는 v7 그대로
                self.assertEqual(old["properties"]["allowed_sections"]["items"]["pattern"], SECTION)
                new = copy.deepcopy(new)
                new["properties"]["allowed_sections"] = old["properties"]["allowed_sections"]
            self.assertEqual(_strip(new, key), {k: v for k, v in old.items() if k not in ("$id", "description")}, name)

    def test_examples(self):
        old = {p.name for p in (V7 / "examples").glob("*.json")}
        new = {p.name for p in (V8 / "examples").glob("*.json")}
        self.assertEqual(new, old | NEW)
        req, res = _validator(V8, "run_request.json"), _validator(V8, "run_result.json")
        for n in sorted(new):
            v = req if n.endswith(".request.json") else res
            self.assertEqual([e.message for e in v.iter_errors(_load(V8 / "examples", n))], [], n)
            if n in old:
                self.assertEqual((V8 / "examples" / n).read_bytes(), (V7 / "examples" / n).read_bytes(), n)
        g = _load(V8 / "examples", "start-exception.result.json")
        self.assertEqual((g["preview"]["kind"], g["preview"]["section"], g["preview"]["reason"]), ("call", g["exception_guide"]["section"], "exception_guide"))
        self.assertEqual(_load(V8 / "examples", "judge-exception.result.json")["decision"]["kind"], "instruct")

    def test_v7_bodies_still_valid(self):
        req, res = _validator(V8, "run_request.json"), _validator(V8, "run_result.json")
        for p in sorted((V7 / "examples").glob("*.json")):
            self.assertTrue((req if p.name.endswith(".request.json") else res).is_valid(_load(V7 / "examples", p.name)), p.name)


class Rejects(unittest.TestCase):   # 실패 쪽: 예외 id 꼴 밖 · 안내 칸 빠짐 · 모르는 안내 칸 · v7은 새 칸 모름
    REQ, RES = _validator(V8, "run_request.json"), _validator(V8, "run_result.json")

    def test_bad(self):
        j = _load(V8 / "examples", "judge-exception.request.json")
        self.assertFalse(self.REQ.is_valid({**j, "exception_relation": "2.3"}))
        g = _load(V8 / "examples", "start-exception.result.json")
        x = copy.deepcopy(g)
        del x["exception_guide"]["text"]
        self.assertFalse(self.RES.is_valid(x), "text 필수")
        self.assertFalse(self.RES.is_valid({**g, "exception_guide": {**g["exception_guide"], "why": "x"}}))
        self.assertFalse(self.RES.is_valid({**g, "exception_guide": {**g["exception_guide"], "grade": "E"}}))
        r = _load(V8 / "examples", "report-exception.request.json")
        for bad in ("EX-", "R-0001", "4.3절", "EX-R 0001"):   # 넓힌 꼴 밖(빈 EX · 기록 ID만 · 글 붙음 · 빈칸)
            self.assertFalse(self.REQ.is_valid({**r, "allowed_sections": ["1.2", bad]}), bad)

    def test_v7_rejects_new_fields(self):
        self.assertFalse(_validator(V7, "run_request.json").is_valid(_load(V8 / "examples", "judge-exception.request.json")))
        self.assertFalse(_validator(V7, "run_result.json").is_valid(_load(V8 / "examples", "start-exception.result.json")))
        self.assertFalse(_validator(V7, "run_request.json").is_valid(_load(V8 / "examples", "report-exception.request.json")))   # v7은 EX 절을 모름(F1 422 꼴)


if __name__ == "__main__":
    unittest.main()
