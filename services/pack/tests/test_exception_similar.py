"""D-566 · 약속 v10 kind exception_similar — 보고서 예외 칸 AI 나눔 결과(예외 하나: 주제 + 점검 목록 + 조치 목록) → 예외 통째로 한 번 그 증상군 등록 예외 · 매뉴얼 점검 · 조치 절 중 비슷한 것(호출 0 · 그래프 파일만).
꼴 = orchestration agents/c1a15 handoff/d566/contract-v10.md 3절."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "pack"
sys.path.insert(0, str(PACK))
from custom import graph_query as G  # noqa: E402
from custom import similar  # noqa: E402
from packrt.run.engine import Runtime  # noqa: E402
from test_pack import need_vector_db  # noqa: E402

V10 = ROOT / "contracts/v10"
COVER = {"topic": "필터 덮개 나사", "checks": [{"what": "필터 덮개 고정 나사", "how": "덮개 나사 네 개를 손으로 돌려 본다", "criteria": "네 개 모두 체결 = 정상", "minutes": None}],
         "actions": [{"what": "필터 덮개 나사 다시 조임", "parts": [], "minutes": None}]}
COOL = {"topic": "냉각수 수위", "checks": [{"what": "냉각수 수위", "how": "냉각수 탱크 눈금을 본다", "criteria": "눈금 아래면 이상"}], "actions": [{"what": "냉각수 보충"}]}
BOTH = {"topic": "필터 덮개 나사", "checks": COVER["checks"] + COOL["checks"], "actions": COVER["actions"] + COOL["actions"]}   # 점검 둘 · 조치 둘인 예외 하나
REQ = {"kind": "exception_similar", "session_id": "S-1", "group": "CL", "exception": BOTH}


def _with_exception():
    rel = copy.deepcopy(G.release())
    rel["sections"].append({"no": "EX-R-9001", "app_no": "EX-R-9001", "kind": "예외", "grade": "C", "name": "필터 덮개 고정 나사 확인", "guide": "덮개 나사를 확인하고 빠졌으면 새로 끼운다",
                            "steps": ["덮개 나사 확인"], "criteria": "나사 네 개 체결 = 정상", "group": "온도 차 저하", "minutes": 5, "checklist": [], "records": ["R-9001"]})
    return rel


class Similar(unittest.TestCase):
    def _find(self, rel, req=REQ):
        real, cur = G._read, str(G.CURRENT.resolve())
        G.release.cache_clear()
        self.addCleanup(G.release.cache_clear)
        with mock.patch.object(G, "_read", lambda p: rel if p == cur else real(p)):
            return similar.find(req)["result"]["exception_similar"]

    def test_registered_exception_and_manual(self):   # 사람 손 길: 덮개 나사 예외 → 등록 예외 1위 · 냉각수 예외 → 매뉴얼 냉각수 절 1위 · 예외 통째로 한 번(상위 3 · 높은 점수 먼저)
        rel = _with_exception()
        cover, cool = self._find(rel, {**REQ, "exception": COVER}), self._find(rel, {**REQ, "exception": COOL})
        self.assertEqual((cover["matches"][0]["kind"], cover["matches"][0]["no"]), ("예외", "EX-R-9001"))
        self.assertEqual(cool["matches"][0]["kind"], "매뉴얼")
        self.assertIn(cool["matches"][0]["no"], ("4.5", "4.5.2"))   # 냉각수 수위 확인 · 냉각수 보충
        both = self._find(rel)
        self.assertEqual(set(both), {"matches"})   # D-566: item마다가 아니라 한 번
        for x in (cover, cool, both):
            s = [m["score"] for m in x["matches"]]
            self.assertTrue(len(s) <= 3 and s == sorted(s, reverse=True) and all(0 < v <= 1 for v in s))
        self.assertIn("EX-R-9001", [m["no"] for m in both["matches"]])

    def test_old_shape_exception_section(self):   # 그래프 예외 절이 v10 목록(checks · actions)이든 옛 하나 꼴(check · action)이든 같은 글로 셈
        rel = _with_exception()
        listed = copy.deepcopy(rel)
        listed["sections"][-1].update({"checks": COVER["checks"], "actions": COVER["actions"]})
        single = copy.deepcopy(rel)
        single["sections"][-1].update({"check": COVER["checks"][0], "action": COVER["actions"][0]})
        self.assertEqual(self._find(listed, {**REQ, "exception": COVER}), self._find(single, {**REQ, "exception": COVER}))

    def test_exception_match_carries_lists(self):   # D-560 덧 10: '같음 + 고침' 바탕 — 등록 예외 줄엔 그 예외의 지금 checks · actions(목록 · 옛 하나 꼴 모두) · 매뉴얼 줄은 칸 0 · v10 결과 꼴
        res = jsonschema.Draft202012Validator(json.loads((V10 / "run_result.json").read_text(encoding="utf-8")))
        listed = _with_exception()
        listed["sections"][-1].update({"checks": [{**COVER["checks"][0], "minutes": 5}, {**COOL["checks"][0], "minutes": 3}],
                                       "actions": [{**COVER["actions"][0], "minutes": 5}]})
        out = self._find(listed, {**REQ, "exception": COVER})
        ex = next(m for m in out["matches"] if m["kind"] == "예외")
        self.assertEqual((ex["checks"], ex["actions"]), (listed["sections"][-1]["checks"], listed["sections"][-1]["actions"]))
        self.assertFalse([m for m in out["matches"] if m["kind"] == "매뉴얼" and ({"checks", "actions"} & set(m))])
        self.assertEqual([e.message for e in res.iter_errors({"exception_similar": out})], [])
        single = _with_exception()
        single["sections"][-1].update({"check": {**COVER["checks"][0], "minutes": 5}, "action": {**COVER["actions"][0], "minutes": 5}})
        ex1 = next(m for m in self._find(single, {**REQ, "exception": COVER})["matches"] if m["kind"] == "예외")
        self.assertEqual((len(ex1["checks"]), len(ex1["actions"])), (1, 1))   # 옛 하나 꼴 → 목록 하나
        plain = next(m for m in self._find(_with_exception(), {**REQ, "exception": COVER})["matches"] if m["kind"] == "예외")
        self.assertFalse({"checks", "actions"} & set(plain))   # 목록 없는 옛 절은 칸 0(앞 출력 그대로)
        self.assertFalse(res.is_valid({"exception_similar": {"matches": [{**ex, "checks": [{"what": "x"}]}]}}))   # 실패 쪽: 칸 꼴 밖

    def test_only_that_group(self):   # 다른 증상군 절 · 다른 증상군 예외는 안 나옴 · 안전 · 사전 확인 절도 안 나옴
        rel = _with_exception()
        nos = {m["no"] for m in self._find(rel, {**REQ, "group": "TW"})["matches"]}
        self.assertNotIn("EX-R-9001", nos)
        self.assertFalse(nos & {"4.3", "4.4", "4.5", "4.6", "4.2"})
        self.assertNotIn("4.2", {m["no"] for m in self._find(rel)["matches"]})   # 사전 확인 절 밖

    def test_min_score(self):   # D-556 덧 5: 최소 점수 밑은 뺌 — 경계 값과 같으면 남김 · 조금 위면 빠짐 · 모두 밑이면 빈 matches(v10 결과 꼴 그대로)
        rel = _with_exception()
        top = self._find(rel)["matches"][0]
        with mock.patch.object(similar, "MIN_SCORE", top["score"]):
            self.assertEqual(self._find(rel)["matches"][0], top)
        with mock.patch.object(similar, "MIN_SCORE", top["score"] + 0.0001):
            self.assertNotIn(top["no"], [m["no"] for m in self._find(rel)["matches"]])
        with mock.patch.object(similar, "MIN_SCORE", 1.0001):
            out = self._find(rel)
        self.assertEqual(out, {"matches": []})
        res = jsonschema.Draft202012Validator(json.loads((V10 / "run_result.json").read_text(encoding="utf-8")))
        self.assertTrue(res.is_valid({"exception_similar": out}))

    def test_min_score_value(self):   # 덧 6 정한 값 0.15 · 실패 쪽: 정비와 엉뚱한 글 → 빈 matches(앱은 새 예외로) · 정비 글은 그대로 나옴
        self.assertEqual(similar.MIN_SCORE, 0.15)
        odd = {**REQ, "exception": {"topic": "교대", "checks": [{"what": "작업자 교대 시간", "how": "교대 일지를 확인한다", "criteria": "교대 시간 맞음 = 정상"}], "actions": [{"what": "교대 일지 서명"}]}}
        with mock.patch.object(similar, "MIN_SCORE", 0.0):
            self.assertTrue(self._find(_with_exception(), odd)["matches"])   # 거르지 않으면 낮은 점수로 걸림
        self.assertEqual(self._find(_with_exception(), odd), {"matches": []})
        self.assertTrue(all(m["score"] >= 0.15 for m in self._find(_with_exception())["matches"]))
        self.assertTrue(self._find(_with_exception())["matches"])

    def test_runtime_output_matches_v10(self):   # 실제 꼴: Pack Runtime 한 번 → 약속 v10 결과 꼴 · 실패 쪽: exception 없음 · v9 items 꼴 · judge에 exception · 한쪽 0 → 거절
        need_vector_db(self)
        res = jsonschema.Draft202012Validator(json.loads((V10 / "run_result.json").read_text(encoding="utf-8")))
        req = jsonschema.Draft202012Validator(json.loads((V10 / "run_request.json").read_text(encoding="utf-8")))
        out = Runtime(PACK).run(REQ)
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        self.assertEqual([e.message for e in res.iter_errors(out["output"])], [])
        self.assertEqual(set(out["output"]["exception_similar"]), {"matches"})
        self.assertEqual(Runtime(PACK).run({k: v for k, v in REQ.items() if k != "exception"})["error"]["code"], "input_invalid")
        self.assertFalse(req.is_valid({**{k: v for k, v in REQ.items() if k != "exception"}, "items": [{"check": COVER["checks"][0], "action": COVER["actions"][0]}]}))   # v9 items 꼴은 v10에서 거절
        judge = json.loads((V10 / "examples/judge.request.json").read_text(encoding="utf-8"))
        self.assertFalse(req.is_valid({**judge, "exception": BOTH}))
        self.assertFalse(req.is_valid({**REQ, "exception": {**BOTH, "actions": []}}))   # 한쪽 0
        self.assertFalse(req.is_valid({**REQ, "exception": {**BOTH, "checks": [{"what": "x", "how": "y"}]}}))   # 기준 칸 빠짐


if __name__ == "__main__":
    unittest.main()
