"""D-556 · 약속 v9 — 점검 바꾸기(switch_check) · 예외 진행(exception_start) 판정 시험(Pack 함수 · 호출 0).
꼴 = orchestration agents/c1a15 handoff/d556/contract-v9.md 1 · 2절. 냉각(CL): 사전 확인 4.2 → 추천 4.3(A · CL-1) · 4.4(C) · 4.5(A · CL-3) · 4.6(A)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pack"))
from custom import judge, switch  # noqa: E402

REQ = json.loads((ROOT / "contracts/v8/examples/judge.request.json").read_text(encoding="utf-8"))
SAFE = [{"type": "lock"}, {"type": "precheck_done"}]


def run(*events):
    return judge.judge({**REQ, "events": [{"seq": i + 1, **e} for i, e in enumerate(events)]})["result"]["decision"]


class Switch(unittest.TestCase):
    def test_pick_candidate_goes_there(self):   # 사람 손 길: 추천 4.3 → 후보 4.5 고름 → 4.5 지시(등급 A 그대로) · switched 칸
        self.assertEqual(run(*SAFE)["section"], "4.3")
        d = run(*SAFE, {"type": "switch_check", "section": "4.5", "from_section": "4.3", "choice": "candidate"})
        self.assertEqual((d["kind"], d["section"], d["cause"], d["switched"]), ("instruct", "4.5", "CL-3", {"from": "4.3", "to": "4.5"}))

    def test_grade_kept(self):   # 고른 절 등급 그대로: C → 파트장 호출 → 승인 뒤 지시
        sw = {"type": "switch_check", "section": "4.4", "from_section": "4.3", "choice": "candidate"}
        d = run(*SAFE, sw)
        self.assertEqual((d["kind"], d["section"], d["reason"]), ("call", "4.4", "switched_check"))
        self.assertEqual(run(*SAFE, sw, {"type": "call_sent", "kind": "approval", "section": "4.4"})["call_status"], "sent")
        self.assertEqual(run(*SAFE, sw, {"type": "call_answer", "answer": "approved", "section": "4.4"})["kind"], "instruct")

    def test_safety_first(self):   # 안전 단계 건너뜀 0: 잠금 · 사전 확인 전에 바꾸면 잠금 · 사전 확인이 먼저
        sw = {"type": "switch_check", "section": "4.5", "from_section": "4.3", "choice": "candidate"}
        self.assertEqual(run(sw)["kind"], "lock")
        self.assertEqual(run({"type": "lock"}, sw)["kind"], "precheck")
        self.assertEqual(run({"type": "lock"}, sw, {"type": "precheck_done"})["section"], "4.5")

    def test_continue_from_chosen(self):   # 고른 점검에서 이어 감: 이상 → 그 원인 조치 · 정상 → 다음 점검(건너뛴 4.3은 '안 함' — 정상으로 세지 않아 다시 추천)
        sw = {"type": "switch_check", "section": "4.5", "from_section": "4.3", "choice": "candidate"}
        bad = run(*SAFE, sw, {"type": "result", "section": "4.5", "value": "이상"})
        self.assertEqual((bad["kind"], bad["section"], bad["cause"]), ("action_after_checklist", "4.5.2", "CL-3"))
        ok = run(*SAFE, sw, {"type": "result", "section": "4.5", "value": "정상"})
        self.assertEqual((ok["kind"], ok["section"]), ("instruct", "4.3"))
        self.assertNotIn("switched", ok)   # 결과가 온 뒤에는 바꿈 칸 0(보통 판정)
        self.assertNotIn("CL-3", ok["probabilities"])   # 고른 점검 결과는 셈에 듦(정상 → 그 원인 후보에서 빠짐)
        self.assertIn("CL-1", ok["probabilities"])   # 건너뛴 4.3의 원인은 그대로 후보('안 함' ≠ 정상)

    def test_bad_switch_refused(self):   # 실패 쪽: 조치 절 · 안전 절 · 다른 증상군 점검 · 없는 절로 바꿈 → 거절(Run 실패)
        for s in ("4.3.2", "1.2", "2.3", "9.9"):
            with self.assertRaises(switch.SwitchError, msg=s):
                run(*SAFE, {"type": "switch_check", "section": s, "from_section": "4.3", "choice": "candidate"})


class ExceptionProgress(unittest.TestCase):
    def test_call_first(self):   # [예외] 호출 후 조치: 파트장 호출(C · 승인 전 조치 지시 0) → 승인 → 조치 → 재가동 → 재가동 값 → 보고서(원인 미상) → 닫힘
        st = {"type": "exception_start", "mode": "call_first"}
        d = run(*SAFE, st)
        self.assertEqual((d["kind"], d["section"], d["grade"], d["reason"], d["exception_progress"]), ("call", "EX-NEW", "C", "exception_progress", {"mode": "call_first"}))
        self.assertEqual(run(*SAFE, st, {"type": "call_sent", "kind": "approval", "section": "EX-NEW"})["call_status"], "sent")
        ok = {"type": "call_answer", "answer": "approved", "section": "EX-NEW"}
        a = run(*SAFE, st, ok)
        self.assertEqual((a["kind"], a["section"], a["stage"], a.get("lead_notice")), ("action", "EX-NEW", "exception_progress", None))
        done = [*SAFE, st, ok, {"type": "action_done", "section": "EX-NEW"}]
        self.assertEqual(run(*done)["kind"], "restart_check")
        self.assertEqual(run(*done, {"type": "restart", "section": "6.1"})["kind"], "restart_value")
        rep = run(*done, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "within"})
        self.assertEqual((rep["kind"], rep["outcome"], rep["cause"]), ("report", "unknown", None))
        self.assertEqual(run(*done, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "within"}, {"type": "report_confirm"})["kind"], "closed")

    def test_report_rejected_reopens(self):   # D-560(정답지 20쪽): 예외 진행 세션도 반려 → 보고서 다시 열림(report) · 다시 확정 → closed · 보통 세션과 같은 꼴
        for mode, start in (("call_first", [{"type": "exception_start", "mode": "call_first"}, {"type": "call_answer", "answer": "approved", "section": "EX-NEW"}]),
                            ("act_now", [{"type": "exception_start", "mode": "act_now"}])):
            done = [*SAFE, *start, {"type": "action_done", "section": "EX-NEW"}, {"type": "restart", "section": "6.1"}, {"type": "restart_result", "outcome": "within"}]
            self.assertEqual(run(*done, {"type": "report_confirm"})["kind"], "closed", mode)
            back = run(*done, {"type": "report_confirm"}, {"type": "report_rejected"})
            self.assertEqual((back["kind"], back["outcome"], back["cause"]), ("report", "unknown", None), mode)
            self.assertEqual(back, run(*done), mode)   # 확정 전 보고서 단계와 같음
            self.assertEqual(run(*done, {"type": "report_confirm"}, {"type": "report_rejected"}, {"type": "report_confirm"})["kind"], "closed", mode)
        plain = [*SAFE, {"type": "result", "section": "4.3", "value": "이상"}]
        self.assertEqual(run(*plain, {"type": "report_rejected"}), judge._decide({**REQ, "events": [{"seq": i + 1, **e} for i, e in enumerate([*plain, {"type": "report_rejected"}])]}))   # 예외 없는 세션은 덧층 손 0

    def test_act_now(self):   # [예외] 바로 조치: 호출 없이 조치 · 파트장에게 '호출 없이 조치함'
        a = run(*SAFE, {"type": "exception_start", "mode": "act_now"})
        self.assertEqual((a["kind"], a["section"], a["lead_notice"], a["exception_progress"]), ("action", "EX-NEW", "acted_without_call", {"mode": "act_now"}))

    def test_safety_and_bad_mode(self):   # 안전 먼저 · 실패 쪽: 모르는 mode → 거절
        st = {"type": "exception_start", "mode": "act_now"}
        self.assertEqual(run(st)["kind"], "lock")
        self.assertEqual(run({"type": "lock"}, st)["kind"], "precheck")
        with self.assertRaises(switch.SwitchError):
            run(*SAFE, {"type": "exception_start", "mode": "later"})

    def test_no_new_events_same_as_v8(self):   # 새 사건이 없으면 판정 그대로(v8과 같음)
        for evs in ([], SAFE, [*SAFE, {"type": "result", "section": "4.3", "value": "이상"}]):
            self.assertEqual(run(*evs), judge._decide({**REQ, "events": [{"seq": i + 1, **e} for i, e in enumerate(evs)]}))


if __name__ == "__main__":
    unittest.main()
