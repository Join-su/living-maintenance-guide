"""약속 v7(contracts/v7 = v6 + 요청 선택 칸 셋 equipment_id · occurred_at · line_id, D-543 덧 3) 시험.
요청 schema는 칸 셋만 더함 · 결과 schema = v6 바이트 · 예시 = v6 바이트 + start-context 하나 · v1 ~ v6은 그대로."""
import copy
import json
import unittest

import jsonschema

from contract_cases import CONTRACTS

V6, V7 = CONTRACTS / "v6", CONTRACTS / "v7"
NEW = ("equipment_id", "occurred_at", "line_id")


def _load(v, name):
    return json.loads((v / name).read_text(encoding="utf-8"))


def _validator(v, name):
    s = _load(v, name)
    jsonschema.Draft202012Validator.check_schema(s)
    return jsonschema.Draft202012Validator(s)


class Shape(unittest.TestCase):
    def test_result_is_v6_bytes(self):
        self.assertEqual((V7 / "run_result.json").read_bytes(), (V6 / "run_result.json").read_bytes())

    def test_request_adds_three_optional_fields_only(self):
        old, new = _load(V6, "run_request.json"), _load(V7, "run_request.json")
        self.assertEqual(new["$id"], "c1a15-pack/contracts/v7/run_request.json")
        self.assertEqual(set(new["properties"]) - set(old["properties"]), set(NEW))
        self.assertEqual(new["required"], old["required"])
        strip = lambda d: {k: v for k, v in d.items() if k not in ("$id", "description")}   # noqa: E731
        n2 = copy.deepcopy(new)
        for k in NEW:
            del n2["properties"][k]
        self.assertEqual(strip(n2), strip(old))

    def test_examples(self):
        old = {p.name for p in (V6 / "examples").glob("*.json")}
        new = {p.name for p in (V7 / "examples").glob("*.json")}
        self.assertEqual(new, old | {"start-context.request.json"})
        req, res = _validator(V7, "run_request.json"), _validator(V7, "run_result.json")
        for n in sorted(new):
            v = req if n.endswith(".request.json") else res
            self.assertEqual([e.message for e in v.iter_errors(_load(V7 / "examples", n))], [], n)
            if n in old:
                self.assertEqual((V7 / "examples" / n).read_bytes(), (V6 / "examples" / n).read_bytes(), n)
        ctx = _load(V7 / "examples", "start-context.request.json")
        self.assertEqual({k: v for k, v in ctx.items() if k not in NEW}, _load(V6 / "examples", "start.request.json"))

    def test_v6_requests_still_valid(self):   # 칸이 없는 옛 요청도 v7에 맞음
        req = _validator(V7, "run_request.json")
        for p in sorted((V6 / "examples").glob("*.request.json")):
            self.assertTrue(req.is_valid(_load(V6 / "examples", p.name)), p.name)


class Rejects(unittest.TestCase):   # 실패 쪽: 시간대 없는 시각 · 초 없는 시각 · 설비 id 꼴 밖 · 모르는 칸은 거절
    REQ = _validator(V7, "run_request.json")
    BASE = _load(V7 / "examples", "start-context.request.json")

    def test_bad_values(self):
        for k, v in (("occurred_at", "2026-11-03T21:15:00"), ("occurred_at", "2026-11-03 21:15"), ("equipment_id", "M 02"), ("line_id", ""), ("equipment_id", 2)):
            self.assertFalse(self.REQ.is_valid({**self.BASE, k: v}), f"{k}")

    def test_unknown_field(self):
        self.assertFalse(self.REQ.is_valid({**self.BASE, "machine": "M-02"}))


class OldKept(unittest.TestCase):   # v6은 그대로(새 칸 모름)
    def test_v6_rejects_new_fields(self):
        self.assertFalse(_validator(V6, "run_request.json").is_valid(_load(V7 / "examples", "start-context.request.json")))


if __name__ == "__main__":
    unittest.main()
