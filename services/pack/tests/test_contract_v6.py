"""약속 v6(contracts/v6 = v5 + alarm 칸 candidate → no_alarm_reason, D-529 · D-530 덧 2 · 앱 dev-2 f33e12b) 시험.
요청 schema = v5 바이트 · 결과 schema는 alarm 칸 하나만 바뀜 · 예시는 후보 꼴 0 · ML 알람 없음 예시 둘 = 앱 route() 실제 출력 꼴. v1 ~ v5는 그대로."""
import copy
import json
import unittest

import jsonschema

from contract_cases import CONTRACTS

V5, V6 = CONTRACTS / "v5", CONTRACTS / "v6"
REASONS = ["low_confidence", "rnf", "model_failure", None]
NEW = {"alarm-ml-low-confidence.result.json", "alarm-ml-model-failure.result.json"}
APP_LINES = [   # 앱 f33e12b route() 실제 실행 두 줄(답장 013-140-go · 앱 보고 001-zzp) — 그대로
    {"alarm": False, "group": None, "group_source": None, "ml": {"model_release": "mr-ai4i-lgbm-large-v1", "risk": 0.7, "alarm": True, "top_k": [
        {"type": "HDF", "probability": 0.45, "group_suggestion": "CL"}, {"type": "PWF", "probability": 0.25, "group_suggestion": "PW"}],
        "confidence": 0.45, "threshold": 0.5, "confirmed": False}, "no_alarm_reason": "low_confidence"},
    {"alarm": False, "group": None, "group_source": None, "ml": None, "no_alarm_reason": "model_failure"},
]


def _load(v, name):
    return json.loads((v / name).read_text(encoding="utf-8"))


def _validator(v, name):
    s = _load(v, name)
    jsonschema.Draft202012Validator.check_schema(s)
    return jsonschema.Draft202012Validator(s)


class Shape(unittest.TestCase):
    def test_request_is_v5_bytes(self):
        self.assertEqual((V6 / "run_request.json").read_bytes(), (V5 / "run_request.json").read_bytes())

    def test_result_changes_only_alarm_reason(self):
        old, new = _load(V5, "run_result.json"), _load(V6, "run_result.json")
        self.assertEqual(new["$id"], "c1a15-pack/contracts/v6/run_result.json")
        oa, na = old["properties"]["alarm"], new["properties"]["alarm"]
        self.assertEqual(na["required"], [("no_alarm_reason" if k == "candidate" else k) for k in oa["required"]])
        self.assertEqual(na["properties"]["no_alarm_reason"]["enum"], REASONS)
        self.assertNotIn("candidate", na["properties"])
        self.assertEqual({k: v for k, v in na["properties"].items() if k != "no_alarm_reason"}, {k: v for k, v in oa["properties"].items() if k != "candidate"})
        self.assertEqual({k: v for k, v in na.items() if k not in ("required", "properties")}, {k: v for k, v in oa.items() if k not in ("required", "properties")})
        strip = lambda d: {k: v for k, v in d.items() if k not in ("$id", "description")}   # noqa: E731
        o2, n2 = copy.deepcopy(old), copy.deepcopy(new)
        del o2["properties"]["alarm"], n2["properties"]["alarm"]
        self.assertEqual(strip(n2), strip(o2))

    def test_examples(self):
        old = {p.name for p in (V5 / "examples").glob("*.json")}
        new = {p.name for p in (V6 / "examples").glob("*.json")}
        self.assertEqual(new, (old - {"alarm-ml-candidate.result.json"}) | NEW)
        req, res = _validator(V6, "run_request.json"), _validator(V6, "run_result.json")
        for n in sorted(new):
            v = req if n.endswith(".request.json") else res
            self.assertEqual([e.message for e in v.iter_errors(_load(V6 / "examples", n))], [], n)
            if n not in NEW and n != "alarm-boundary.result.json":
                self.assertEqual((V6 / "examples" / n).read_bytes(), (V5 / "examples" / n).read_bytes(), n)
        b5, b6 = _load(V5 / "examples", "alarm-boundary.result.json"), _load(V6 / "examples", "alarm-boundary.result.json")
        self.assertEqual(list(b6["alarm"]), [("no_alarm_reason" if k == "candidate" else k) for k in b5["alarm"]])
        self.assertIsNone(b6["alarm"]["no_alarm_reason"])
        self.assertEqual({k: v for k, v in b6["alarm"].items() if k != "no_alarm_reason"}, {k: v for k, v in b5["alarm"].items() if k != "candidate"})

    def test_no_alarm_examples_are_app_lines(self):   # 칸 다섯 = 앱 route() 실제 출력 그대로 + Pack 알람 칸(event_id · alarm_block · sensor_derived)
        for n, line in zip(sorted(NEW), [APP_LINES[0], APP_LINES[1]]):
            a = _load(V6 / "examples", n)["alarm"]
            self.assertEqual({k: v for k, v in a.items() if k not in ("event_id", "alarm_block", "sensor_derived")}, line, n)

    def test_no_candidate_anywhere(self):
        for p in (V6 / "examples").glob("*.result.json"):
            self.assertNotIn("candidate", json.dumps(_load(V6 / "examples", p.name)), p.name)


class Rejects(unittest.TestCase):   # 실패 쪽: 옛 꼴 · 모르는 사유 · 사유 칸 없음은 거절
    RES = _validator(V6, "run_result.json")

    def test_old_candidate_shape_rejected(self):
        self.assertFalse(self.RES.is_valid(_load(V5 / "examples", "alarm-ml-candidate.result.json")))
        x = _load(V6 / "examples", "alarm-boundary.result.json")
        x["alarm"]["candidate"] = None
        self.assertFalse(self.RES.is_valid(x), "candidate 칸 없음")

    def test_reason_values(self):
        x = _load(V6 / "examples", "alarm-ml-low-confidence.result.json")
        for r in REASONS:
            self.assertTrue(self.RES.is_valid({"alarm": {**x["alarm"], "no_alarm_reason": r}}), r)
        self.assertFalse(self.RES.is_valid({"alarm": {**x["alarm"], "no_alarm_reason": "other"}}), "사유 셋 + null만")
        y = copy.deepcopy(x)
        del y["alarm"]["no_alarm_reason"]
        self.assertFalse(self.RES.is_valid(y), "no_alarm_reason 필수")


class OldKept(unittest.TestCase):   # v1 ~ v5는 그대로(검토된 판)
    def test_v5_still_has_candidate(self):
        self.assertIn("candidate", _load(V5, "run_result.json")["properties"]["alarm"]["required"])
        self.assertTrue((V5 / "examples/alarm-ml-candidate.result.json").is_file())


if __name__ == "__main__":
    unittest.main()
