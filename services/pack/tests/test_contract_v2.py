"""약속 v2(contracts/v2 = v1 + D-264 event_card.product_type) 시험 — v1 사례 전부 + product_type 사례."""
import copy
import json
import unittest

from contract_cases import CONTRACTS, cases

Examples, RequestRejects, ResultRejects, load, REQ, RES = cases("v2")


class ProductType(unittest.TestCase):   # D-264
    def test_ml_path_requires_product_type(self):
        m = load("alarm-ml.request.json")
        self.assertTrue(REQ.is_valid(m))
        x = copy.deepcopy(m)
        del x["event_card"]["product_type"]
        self.assertFalse(REQ.is_valid(x), "ML 길은 제품 등급 필수")
        x = copy.deepcopy(m)
        x["event_card"]["product_type"] = "X"
        self.assertFalse(REQ.is_valid(x), "L · M · H만")

    def test_boundary_path_allows_either(self):
        b = load("alarm-boundary.request.json")
        self.assertTrue(REQ.is_valid(b))
        x = copy.deepcopy(b)
        del x["event_card"]["product_type"]
        self.assertTrue(REQ.is_valid(x), "경계 길은 없어도 됨")

    def test_only_difference_from_v1(self):
        v1 = json.loads((CONTRACTS / "v1" / "run_result.json").read_text(encoding="utf-8"))
        v2 = json.loads((CONTRACTS / "v2" / "run_result.json").read_text(encoding="utf-8"))
        for d in (v1, v2):
            d.pop("$id"); d.pop("description")
        self.assertEqual(v1, v2, "출력 모양은 v1과 같음")


if __name__ == "__main__":
    unittest.main()
