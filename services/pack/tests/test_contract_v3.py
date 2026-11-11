"""약속 v3(contracts/v3 = v2 + kind report, D-285) 시험 — v2 공통 사례 전부(예시 수 12) + report 사례."""
import copy
import json
import unittest

from contract_cases import CONTRACTS, cases

_examples_v2_count, RequestRejects, ResultRejects, load, REQ, RES = cases("v3")
del _examples_v2_count   # 공통 예시 수 시험(10개)은 v3(12개)에 맞지 않음 — 아래 Examples가 대신


class Examples(unittest.TestCase):
    def test_every_example_matches(self):
        names = sorted(p.name for p in (CONTRACTS / "v3" / "examples").glob("*.json"))
        self.assertEqual(len(names), 12)
        for n in names:
            v = REQ if n.endswith(".request.json") else RES
            self.assertEqual([e.message for e in v.iter_errors(load(n))], [], n)


class Report(unittest.TestCase):   # D-285: 보고서 문장 node
    def test_required_and_forbidden(self):
        r = load("report.request.json")
        for k in ("report_draft", "similar", "allowed_sections", "session_id", "group"):
            x = copy.deepcopy(r)
            del x[k]
            self.assertFalse(REQ.is_valid(x), k)
        for extra in ({"events": []}, {"grades": {}}, {"event_card": {}}, {"memo": "원문"}):
            self.assertFalse(REQ.is_valid({**r, **extra}), extra)
        x = copy.deepcopy(r)
        x["report_draft"]["author"] = "tech.kim"
        self.assertFalse(REQ.is_valid(x), "8항목 밖 칸 없음")
        for kind in ("alarm", "start", "judge"):
            base = load({"alarm": "alarm-boundary", "start": "start-bridge", "judge": "judge"}[kind] + ".request.json")
            self.assertFalse(REQ.is_valid({**base, "report_draft": r["report_draft"]}), f"{kind}에 report 칸 섞지 않음")

    def test_result_shape(self):
        rr = load("report.result.json")
        self.assertFalse(RES.is_valid({**rr, "decision": {"kind": "lock"}}))
        x = copy.deepcopy(rr)
        x["report"]["cause"]["attempts"] = 3
        self.assertFalse(RES.is_valid(x), "다시 쓰기는 한 번까지")
        x = copy.deepcopy(rr)
        del x["report"]["action"]["fallback"]
        self.assertFalse(RES.is_valid(x), "원문 대체 표시 필수")

    def test_v2_bytes_of_other_kinds_unchanged(self):   # alarm · start · judge 모양은 v2 그대로
        v2 = json.loads((CONTRACTS / "v2" / "run_request.json").read_text(encoding="utf-8"))
        v3 = json.loads((CONTRACTS / "v3" / "run_request.json").read_text(encoding="utf-8"))
        for k in ("session_id", "event_card", "settings", "event_id", "group", "query", "events", "grades", "minutes"):
            self.assertEqual(v2["properties"][k], v3["properties"][k], k)
        self.assertEqual(v2["$defs"], v3["$defs"])
        for n in ("alarm-boundary", "alarm-ml", "start", "start-bridge", "judge"):
            self.assertTrue(REQ.is_valid(load(f"{n}.request.json")), n)


if __name__ == "__main__":
    unittest.main()
