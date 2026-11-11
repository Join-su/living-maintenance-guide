"""약속 시험 공통(contracts/v<n>) — 예시가 schema에 맞고, 약속을 어긴 입력 · 출력은 거절된다.
배포 서버는 이 schema로만 받는다(일반 방식) — 여기서 거절되는 본문은 배포 서버에서 422 invalid_input.
test_contract_v1 · test_contract_v2가 이 사례를 같은 version 폴더로 돌린다."""
import copy
import json
import unittest
from pathlib import Path

import jsonschema

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def cases(version):
    V = CONTRACTS / version
    EX = V / "examples"

    def load(name):
        return json.loads((EX / name).read_text(encoding="utf-8"))

    def validator(name):
        schema = json.loads((V / name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        return jsonschema.Draft202012Validator(schema)

    REQ, RES = validator("run_request.json"), validator("run_result.json")

    class Examples(unittest.TestCase):
        def test_every_example_matches(self):
            names = sorted(p.name for p in EX.glob("*.json"))
            self.assertEqual(len(names), 10)
            for n in names:
                v = REQ if n.endswith(".request.json") else RES
                errs = [e.message for e in v.iter_errors(load(n))]
                self.assertEqual(errs, [], n)


    class RequestRejects(unittest.TestCase):
        def bad(self, body, why):
            self.assertFalse(REQ.is_valid(body), why)

        def test_alarm(self):
            b = load("alarm-boundary.request.json")
            x = copy.deepcopy(b)
            x["event_card"]["sensor_snapshot"]["power_w"] = 7864
            self.bad(x, "계산 칸(출력)은 입력에 넣지 않음")
            x = copy.deepcopy(b)
            del x["event_card"]["sensor_snapshot"]["torque_nm"]
            self.bad(x, "센서 5칸 필수")
            x = copy.deepcopy(b)
            x["event_card"]["alarm"] = {"source": "synthetic_external_alarm", "code": "CL"}
            self.bad(x, "알람은 입력이 아님")
            x = copy.deepcopy(b)
            x["event_card"]["machine_failure"] = 1
            self.bad(x, "고장 라벨 칸 없음")
            x = copy.deepcopy(b)
            x["settings"] = {"ml_alarm_threshold": 0.5}
            self.bad(x, "경계 길에는 문턱을 보내지 않음")
            x = copy.deepcopy(b)
            x["session_id"] = "S-1"
            self.bad(x, "알람 Run에는 세션이 없음")
            x = copy.deepcopy(b)
            x["trend"] = []
            self.bad(x, "추이는 앱 몫")
            m = load("alarm-ml.request.json")
            x = copy.deepcopy(m)
            del x["settings"]
            self.bad(x, "ML 길은 문턱(앱 사용자 설정) 필수")
            for t in (0, 1, 1.2):
                x = copy.deepcopy(m)
                x["settings"]["ml_alarm_threshold"] = t
                self.bad(x, f"문턱 {t}")

        def test_start(self):
            b = load("start.request.json")
            for k in ("session_id", "event_id", "query", "grades"):
                x = copy.deepcopy(b)
                del x[k]
                self.bad(x, f"start {k} 필수")
            x = copy.deepcopy(b)
            x["events"] = [{"seq": 1, "type": "lock"}]
            self.bad(x, "start는 입력 기록 없이")
            x = copy.deepcopy(b)
            x["query"]["sensor_snapshot"]["temp_diff_k"] = 7.8
            self.bad(x, "질의 센서도 5칸")
            x = load("start-bridge.request.json")
            x["similar"] = x["similar"] * 2
            self.bad(x, "상위 10건까지")

        def test_judge(self):
            b = load("judge.request.json")
            for k in ("session_id", "similar", "events"):
                x = copy.deepcopy(b)
                del x[k]
                self.bad(x, f"judge {k} 필수")
            x = copy.deepcopy(b)
            x["query"] = load("start.request.json")["query"]
            self.bad(x, "judge는 다시 찾지 않음")
            x = copy.deepcopy(b)
            x["memo"] = "원문"
            self.bad(x, "메모 원문 칸 없음")
            x = copy.deepcopy(b)
            x["session_id"] = 12
            self.bad(x, "session_id는 문자열")
            self.bad({"kind": "predict", "event_card": load("alarm-ml.request.json")["event_card"]}, "옛 kind predict는 v1에 없음")


    class ResultRejects(unittest.TestCase):
        def test_shapes(self):
            a = load("alarm-boundary.result.json")
            s = load("start.result.json")
            self.assertFalse(RES.is_valid({**a, "decision": {"kind": "lock"}}), "알람 결과에 판정 섞지 않음")
            self.assertFalse(RES.is_valid({"decision": s["decision"], "similar": s["similar"]}), "start 결과는 retrieval까지")
            x = copy.deepcopy(a)
            del x["alarm"]["sensor_derived"]
            self.assertFalse(RES.is_valid(x), "계산 칸을 돌려줌")
            x = copy.deepcopy(load("alarm-ml-candidate.result.json"))
            x["alarm"]["candidate"]["reason"] = "other"
            self.assertFalse(RES.is_valid(x), "후보 이유 셋만")
            self.assertFalse(RES.is_valid({}), "빈 결과 없음")

    return Examples, RequestRejects, ResultRejects, load, REQ, RES
