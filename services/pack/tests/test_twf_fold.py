"""D-526 덧 6(v22): TWF는 고장 예측에서 뺌 — 지금 모델(5클래스) 그대로, ML 길 route()가 P(NONE) += P(TWF) · TWF 칸 지움(앱 alarms/ml.py 사본).
위험도 = 1 − P(NONE)에서 TWF 몫이 빠지고 유형 후보에 TWF 0. 공구 마모는 센서 경계 규칙(215분)이 맡음 — 경계 길은 route를 지나지 않음.
schema prediction.json의 TWF는 남김(predict@1이 모델 출력을 검사). 실패 글은 유형 이름 · 개수만."""
import json
import unittest

import jsonschema

from test_pack import PACK, ROOT, _card, _NeedsVectorDB
from custom import alarm_route

TH = 0.5


def _pred(probs):
    t = max(probs, key=probs.get)
    return {"type": t, "probability": probs[t], "probabilities": dict(probs), "model_release": "mr-test"}


class Fold(unittest.TestCase):
    def test_twf_mass_goes_to_none(self):   # TWF가 가장 큰 예측 → 위험도가 TWF 몫만큼 줄고 알람 없음 · 사유 없음(위험도 < 문턱)
        r = alarm_route.route(_pred({"NONE": 0.3, "TWF": 0.6, "HDF": 0.1}), None, TH)
        self.assertEqual((r["alarm"], r["group"], r["no_alarm_reason"]), (False, None, None))
        self.assertAlmostEqual(r["ml"]["risk"], 0.1)
        self.assertEqual([t["type"] for t in r["ml"]["top_k"]], ["HDF"])

    def test_other_type_still_alarms(self):   # TWF를 빼도 다른 유형은 그대로 — 위험도 ≥ 문턱 · 확신도 ≥ 문턱이면 그 증상군
        r = alarm_route.route(_pred({"NONE": 0.2, "TWF": 0.2, "HDF": 0.6}), None, TH)
        self.assertEqual((r["alarm"], r["group"], r["group_source"]), (True, "CL", "ml_type"))
        self.assertAlmostEqual(r["ml"]["risk"], 0.6)
        self.assertNotIn("TWF", [t["type"] for t in r["ml"]["top_k"]])

    def test_boundary_hit_keeps_boundary_group(self):   # 경계를 넘은 행은 경계 증상군(TW 포함) — TWF 접기와 상관없이 경계 먼저
        r = alarm_route.route(_pred({"NONE": 0.1, "TWF": 0.1, "HDF": 0.8}), {"group": "TW"}, TH)
        self.assertEqual((r["alarm"], r["group"], r["group_source"]), (True, "TW", "sensor_boundary"))

    def test_twf_without_probabilities_is_none(self):   # 실패 쪽: probabilities 없이 type TWF만 오면 NONE으로 — 알람 0 · 사유 없음 · 유형 후보 0
        r = alarm_route.route({"type": "TWF", "probability": 0.9, "model_release": "mr-test"}, None, TH)
        self.assertEqual((r["alarm"], r["no_alarm_reason"], r["ml"]["top_k"]), (False, None, []))
        self.assertAlmostEqual(r["ml"]["risk"], 0.1)

    def test_input_not_changed(self):   # 받은 예측 dict는 그대로(호출한 쪽이 다시 씀)
        p = _pred({"NONE": 0.3, "TWF": 0.6, "HDF": 0.1})
        alarm_route.route(p, None, TH)
        self.assertIn("TWF", p["probabilities"])

    def test_no_twf_group(self):
        self.assertNotIn("TWF", alarm_route.TYPE_TO_GROUP)
        self.assertEqual(alarm_route.TYPE_TO_GROUP, {"HDF": "CL", "PWF": "PW", "OSF": "LD"})

    def test_prediction_schema_keeps_twf(self):   # 되돌림 모델(v1 · 자리 모델 5클래스) 출력 검사 — 빼면 되돌린 predict@1이 Run 실패. 모델 v2(4클래스)는 TWF를 안 냄(D-546)
        enum = json.loads((PACK / "schemas/prediction.json").read_text(encoding="utf-8"))["properties"]["type"]["enum"]
        self.assertIn("TWF", enum)
        m = json.loads((ROOT / "releases/models/mr-ai4i-lgbm-large-v2/manifest.json").read_text(encoding="utf-8"))
        self.assertLessEqual(set(m["classes"]), set(enum))
        self.assertNotIn("TWF", m["classes"])


class PackOutput(_NeedsVectorDB):   # Pack ML 길 출력에 TWF 0(사용자 모델 · 공구 마모 높은 행 포함)
    def test_ml_alarm_output_has_no_twf(self):
        res = jsonschema.Draft202012Validator(json.loads((PACK / "schemas/run_result.json").read_text(encoding="utf-8")))
        cards = [_card(tool_wear_min=w, torque_nm=q) for w in (100.0, 200.0, 230.0, 250.0) for q in (40.0, 55.0, 65.0)]
        seen = 0
        for c in cards:
            out = self.rt.run(c)
            self.assertEqual(out["status"], "succeeded", out.get("error"))
            self.assertEqual([e.message for e in res.iter_errors(out["output"])], [])
            ml = out["output"]["alarm"]["ml"]
            types = [t["type"] for t in ml["top_k"]]   # dev-2(D-530 덧 2): 후보 칸 지움 — 유형은 ml 칸 하나
            self.assertNotIn("TWF", types)
            seen += 1
        self.assertEqual(seen, len(cards))

    def test_contract_example_kept(self):   # 약속 v5 예시 top_k의 TWF는 자리 모델 모양 예시(검토된 판 그대로) — Pack 출력과 견주지 않음
        ex = json.loads((ROOT / "contracts/v5/examples/alarm-ml-candidate.result.json").read_text(encoding="utf-8"))
        self.assertIn("TWF", [t["type"] for t in ex["alarm"]["ml"]["top_k"]])
