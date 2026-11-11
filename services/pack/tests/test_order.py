"""D-565 덧 3 · Pack 옵션 check_first_<증상군>(먼저 볼 점검 하나) 덧층 시험 — 판정 함수에 값을 넘김 + pack.yaml 선언 값을 바꾼 Runtime 한 번(망 0).
꼴 = orchestration agents/c1a15 handoff/d565/check-before.md. 냉각(CL): 사전 확인 4.2 → 추천 4.3(A) · 4.4(C) · 4.5(A) · 4.6(A). 값 = none · s4_5(= 4.5) …"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pack"))
from custom import graph_query as G, judge, order  # noqa: E402
from packrt.run.engine import Runtime  # noqa: E402

REQ = json.loads((ROOT / "contracts/v8/examples/judge.request.json").read_text(encoding="utf-8"))
DECLARED = json.loads((ROOT / "tests/fixtures/declared_options.json").read_text(encoding="utf-8"))   # pack.yaml 옵션 선언 값 기록(test_options와 같은 파일)
CTX = json.loads((ROOT / "contracts/v7/examples/start-context.request.json").read_text(encoding="utf-8"))
SAFE = [{"type": "lock"}, {"type": "precheck_done"}]


def run(*events, first=None, opts=None):
    o = opts if opts is not None else (None if first is None else {"check_first_cl": first})
    return judge.judge({**REQ, "events": [{"seq": i + 1, **e} for i, e in enumerate(events)]}, o)["result"]["decision"]


def start(opts=None):
    return judge.start(CTX, G.query(CTX, judge.history_query(CTX)["history_query"])["history_hits"], options=opts)["result"]


class CheckFirst(unittest.TestCase):
    def test_none_same_as_before(self):   # 기본 none = 지금 출력 그대로(judge · start · preview)
        for evs in ([], SAFE, [*SAFE, {"type": "result", "section": "4.3", "value": "정상"}]):
            self.assertEqual(run(*evs, first="none"), run(*evs))
        self.assertEqual(start({f"check_first_{g}": "none" for g in ("tw", "pw", "cl", "ld")}), start())

    def test_first_moves_check(self):   # 사람 손 길(헛걸음 고침): 4.3 추천 자리에 4.5 · reordered · 후보 · 설명 덧층 · 확률은 그 원인 몫
        self.assertEqual(run(*SAFE)["section"], "4.3")
        d = run(*SAFE, first="s4_5")
        self.assertEqual((d["kind"], d["section"], d["cause"], d["grade"], d["reordered"]), ("instruct", "4.5", "CL-3", "A", {"from": "4.3", "to": "4.5", "by": "check_first_cl"}))
        self.assertEqual([a["section"] for a in d["reason_facts"]["alternatives"]], ["4.3", "4.4", "4.6"])
        self.assertTrue(all("desc" in a for a in d["reason_facts"]["alternatives"]))
        self.assertEqual(d["reason_facts"]["probability"], d["probabilities"]["CL-3"])
        nxt = run(*SAFE, {"type": "result", "section": "4.5", "value": "정상"}, first="s4_5")   # 4.5를 했으면 나머지 차례 그대로(4.3)
        self.assertEqual((nxt["section"], "reordered" in nxt), ("4.3", False))
        self.assertEqual(run(*SAFE, first="s4_3"), run(*SAFE))   # 이미 그 점검이 추천이면 손 0
        s = start({"check_first_cl": "s4_5"})
        self.assertEqual((s["decision"]["kind"], s["preview"]["section"]), ("lock", "4.5"))   # 안전 먼저 · 미리보기도 같은 덧층
        self.assertEqual(run(*SAFE, opts={"check_first_tw": "s2_4"}), run(*SAFE))   # 다른 증상군 옵션은 이 세션에 손 0

    def test_cannot_pick_x(self):   # X를 고를 수 없으면 안 씀: 원인 빠짐(정상 결과) · 등급이 C로 바뀜 · 확인 불가 2회
        gone = run(*SAFE, {"type": "result", "section": "4.5", "value": "정상"}, first="s4_5")
        self.assertNotIn("reordered", gone)
        c = judge.judge({**REQ, "grades": {**REQ["grades"], "4.5": "C"}, "events": [{"seq": i + 1, **e} for i, e in enumerate(SAFE)]}, {"check_first_cl": "s4_5"})["result"]["decision"]
        self.assertEqual((c["section"], "reordered" in c), ("4.3", False))   # 등급 규칙 못 바꿈
        unable = [*SAFE, *[{"type": "result", "section": "4.5", "value": "확인 불가"}] * 2]
        self.assertNotEqual(run(*unable, first="s4_5")["section"], "4.5")

    def test_safety_grade_switch_untouched(self):   # 못 바꿈: 안전 단계 · 승인된 C 먼저 · 점검 바꾸기(사람이 고른 점검)
        self.assertEqual(run(first="s4_5")["kind"], "lock")
        self.assertEqual(run({"type": "lock"}, first="s4_5")["kind"], "precheck")
        appr = [*SAFE, {"type": "call_answer", "answer": "approved", "section": "4.4"}]
        self.assertEqual(run(*appr, first="s4_5"), run(*appr))
        sw = {"type": "switch_check", "section": "4.6", "from_section": "4.3", "choice": "candidate"}
        self.assertEqual(run(*SAFE, sw, first="s4_5")["section"], "4.6")

    def test_reason_says_option_picked(self):   # D-652 12-f: 옵션이 고른 첫 점검이면 이유 재료에 그 사실(옵션 이름 · 정해 둔 절 · 원래 고를 절 · 절 이름 = 매뉴얼) · 실패 쪽: 옵션 없음 · 옵션이 안 바꿈 → 칸 0
        d = run(*SAFE, first="s4_5")
        k = judge.manual()
        self.assertEqual(d["reason_facts"]["picked_by"], {"option": "check_first_cl", "section": "4.5", "title": k.sections["4.5"]["title"], "instead_of": "4.3",
                                                         "instead_title": k.sections["4.3"]["title"]})
        self.assertNotIn("picked_by", run(*SAFE)["reason_facts"])
        self.assertNotIn("picked_by", run(*SAFE, first="s4_3").get("reason_facts") or {})   # 옵션 값 = 원래 첫 점검이면 바꿈 0
        p = start({"check_first_cl": "s4_5"})["preview"]
        self.assertEqual((p["section"], p["reason_facts"]["picked_by"]["option"]), ("4.5", "check_first_cl"))   # 진단 미리보기에도

    def test_bad_values_refused(self):   # 실패 쪽: 다른 증상군 절 · 안전 · 조치 절 · 꼴 밖 · 모르는 증상군 → OrderError(Run 실패)
        for bad in ({"check_first_cl": "s2_3"}, {"check_first_cl": "s4_2"}, {"check_first_cl": "s4_3_2"}, {"check_first_cl": "4.5"}, {"check_first_cl": "x"}, {"check_first_zz": "none"}):
            with self.assertRaises(order.OrderError, msg=str(bad)):
                run(*SAFE, opts=bad)
        with self.assertRaises(order.OrderError):
            run(opts={"check_first_cl": "s9_9"})   # 값 검사는 안전 단계에서도

    def test_declared_option_through_runtime(self):   # 실제 꼴: pack.yaml 선언 네 개 · 값을 s4_5로 바꾼 Runtime(packrt 선언 검사 통과) → judge Run 결정이 4.5
        rt = Runtime(ROOT / "pack")
        names = set()
        for g in rt.doc["graphs"].values():
            for n in g["nodes"]:
                opts = (n.get("params") or {}).get("options") or {}
                names |= {k for k in opts if k.startswith("check_first_")}
                if "check_first_cl" in opts:   # 선언 값 = 선언 기록 파일(받아 넣기 도구가 승인 값으로 다시 씀 · D-645 ⑧ — 'none' 박기 0)
                    self.assertEqual((opts["check_first_cl"]["value"], opts["check_first_cl"]["choices"]), (DECLARED["check_first_cl"], ["none", "s4_3", "s4_5", "s4_6"]))
                    opts["check_first_cl"]["value"] = "s4_5"
        self.assertEqual(names, {"check_first_tw", "check_first_pw", "check_first_cl", "check_first_ld"})
        out = rt.run({**REQ, "session_id": "S-1", "events": [{"seq": i + 1, **e} for i, e in enumerate(SAFE)]})
        self.assertEqual(out["status"], "succeeded", out.get("error"))
        self.assertEqual((out["output"]["decision"]["section"], out["output"]["decision"]["reordered"]["by"]), ("4.5", "check_first_cl"))
        self.assertEqual(order.choice_name("4.5"), "s4_5")
        self.assertEqual(order.section_of("s4_5"), "4.5")


if __name__ == "__main__":
    unittest.main()
