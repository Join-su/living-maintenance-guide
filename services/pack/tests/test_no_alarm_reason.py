"""D-529 · D-530 덧 2(dev-2): ML 길 후보 갈래 지움 — 알람 없음 + 사유 한 칸(no_alarm_reason: low_confidence · rnf · model_failure, 그 밖 null).
route()는 앱 dev-2 f33e12b alarms/ml.py 사본 · Pack 알람 출력 = 약속 v6. 실패 글은 칸 이름 · 사유만."""
import json
import unittest

import jsonschema

from test_pack import PACK, ROOT, _card, _NeedsVectorDB
from custom import alarm_route

KEYS = {"alarm", "group", "group_source", "ml", "no_alarm_reason"}
TH = 0.5


def _pred(probs):
    t = max(probs, key=probs.get)
    return {"type": t, "probability": probs[t], "probabilities": dict(probs), "model_release": "mr-test"}


class Route(unittest.TestCase):   # 다섯 갈래 모두 같은 칸 다섯
    def cases(self):
        return {
            "model_failure": alarm_route.route(None, None, TH),
            "below": alarm_route.route(_pred({"NONE": 0.8, "HDF": 0.2}), None, TH),
            "boundary": alarm_route.route(_pred({"NONE": 0.1, "HDF": 0.9}), {"group": "CL"}, TH),
            "ml_type": alarm_route.route(_pred({"NONE": 0.1, "HDF": 0.9}), None, TH),
            "low_confidence": alarm_route.route(_pred({"NONE": 0.3, "HDF": 0.4, "PWF": 0.3}), None, TH),
            "rnf": alarm_route.route(_pred({"NONE": 0.1, "RNF": 0.9}), None, TH),
        }

    def test_same_keys(self):
        for name, r in self.cases().items():
            self.assertEqual(set(r), KEYS, name)

    def test_reasons(self):
        got = {n: (r["alarm"], r["no_alarm_reason"]) for n, r in self.cases().items()}
        self.assertEqual(got, {"model_failure": (False, "model_failure"), "below": (False, None), "boundary": (True, None),
                               "ml_type": (True, None), "low_confidence": (False, "low_confidence"), "rnf": (False, "rnf")})

    def test_ml_kept_when_no_alarm(self):   # 위험도 · 유형은 ml 칸 그대로(후보 칸이 따로 들고 있던 것)
        r = self.cases()["low_confidence"]
        self.assertAlmostEqual(r["ml"]["risk"], 0.7)
        self.assertEqual([t["type"] for t in r["ml"]["top_k"]], ["HDF", "PWF"])
        self.assertIsNone(self.cases()["model_failure"]["ml"])


class PackOutput(_NeedsVectorDB):   # Pack Run 알람 출력(기본 실행 설정 = 사용자 모델) — 약속 v6
    RES = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v6/run_result.json").read_text(encoding="utf-8")))

    def alarm(self, req):
        out = self.rt.run(req)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        self.assertEqual([e.message for e in self.RES.iter_errors(out["output"])], [])
        return out["output"]["alarm"]

    def test_low_confidence_real_run_same_shape_as_example(self):   # 실제 꼴: 사용자 모델 ONNX 실제 Run → v6 예시와 같은 칸 · 같은 순서
        card = _card(th=0.4, air_temperature_k=299.3, process_temperature_k=307.7, rotational_speed_rpm=1490.0, torque_nm=53.8, tool_wear_min=208.0)
        card["event_card"]["product_type"] = "L"   # 모델 v2: 위험도 ≈ 0.50 · 1등 OSF ≈ 0.26 · 경계 넘음 0(문턱 0.5에선 경계 안 넘는 낮은 확신 행을 찾지 못함 — 시험 문턱 0.4)
        a = self.alarm(card)
        self.assertEqual((a["alarm"], a["no_alarm_reason"]), (False, "low_confidence"))
        self.assertGreaterEqual(a["ml"]["risk"], 0.4)
        self.assertLess(a["ml"]["confidence"], 0.4)
        ex = json.loads((ROOT / "contracts/v6/examples/alarm-ml-low-confidence.result.json").read_text(encoding="utf-8"))["alarm"]
        self.assertEqual(list(a), list(ex))
        self.assertEqual(list(a["ml"]), list(ex["ml"]))

    def test_boundary_path_reason_null(self):
        a = self.alarm(_card("sensor_boundary", process_temperature_k=307.5))
        self.assertEqual((a["alarm"], a["group_source"], a["no_alarm_reason"]), (True, "sensor_boundary", None))
        self.assertNotIn("candidate", a)

    def test_pack_schema_alarm_is_v6(self):   # 알람 칸 꼴은 v6 그대로(v7 · v8 · v9는 요청 칸 · exception_guide · exception_similar · exception_split만 더함 — 바이트 같음은 test_pack Contract가 v9로 봄)
        pack, v6 = (json.loads(p.read_text(encoding="utf-8")) for p in (PACK / "schemas/run_result.json", ROOT / "contracts/v6/run_result.json"))
        self.assertEqual(pack["properties"]["alarm"], v6["properties"]["alarm"])
        self.assertEqual(set(pack["properties"]) - set(v6["properties"]), {"exception_guide", "exception_similar", "exception_split"})


if __name__ == "__main__":
    unittest.main()
