"""D-486 덧 7: 근거 검사 기록 ID — 한글 · 문장부호가 붙은 ID('H-0153입니다')도 기준 밖이면 떨어짐(eval 092 LHR07 · 013-114 ①).
앱 사본(custom/report_grounding.py \\b 꼴 · 사본 sha 표)은 그대로, Pack report.py가 RECORD 꼴로 한 번 더 봄. 요청 = 약속 v3 예시(만든 값 · 정답 파일 0)."""
import json
import unittest

from test_pack import ROOT

from custom import report

OUT = "H-0153"   # 예시 상위 10건 · 초안 밖 기록


def _req():
    return json.loads((ROOT / "contracts/v3/examples/report.request.json").read_text(encoding="utf-8"))


def _cause(text, req=None):
    r = req or _req()
    return report.grounded(r, {"cause": text, "action": r["report_draft"]["action"]})["details"]["cause"]


class RecordAttached(unittest.TestCase):
    def test_example_has_no_out_id(self):
        self.assertNotIn(OUT, [s["record_id"] for s in _req()["similar"]])

    def test_attached_outside_id_fails(self):   # 붙은 꼴(뒤 조사 · 앞 한글 · 괄호 · 쉼표)
        for text in (f"냉각 팬 고장이며, 참고 기록은 {OUT}입니다.", f"냉각 팬 고장이며 기록{OUT}와 같음.",
                     f"냉각 팬 고장(참고 {OUT}).", f"냉각 팬 고장이며, {OUT}, H-0226과 같음."):
            with self.subTest(text=text):
                row = _cause(text)
                self.assertEqual((row["ok"], row["reason"]), (False, "ungrounded_record"))
                self.assertIn(OUT, row["found"])

    def test_rf_draft_id_attached_fails(self):   # LHR07 꼴: 초안 원인 칸이 상위 10건 밖 ID → 그 숫자가 수치 집합에 들어 앱 사본 검사는 지남
        r = _req()
        r["report_draft"]["cause"] = f"냉각 팬 고장(CL-1) (참고 기록 {OUT})"
        row = _cause(f"확정 원인은 냉각 팬 고장이며, 참고 기록은 {OUT}입니다.", r)
        self.assertEqual((row["ok"], row["reason"]), (False, "ungrounded_record"))

    def test_spaced_outside_id_fails_as_before(self):
        self.assertEqual(_cause(f"냉각 팬 고장이며, 참고 기록은 {OUT} 입니다.")["reason"], "ungrounded_record")

    def test_true_id_passes_attached_or_spaced(self):   # 초안 근거 기록 ID는 붙어도 · 띄어도 통과
        for text in ("냉각 팬 고장이며, 근거 기록은 H-0226입니다.", "냉각 팬 고장이며, 근거 기록은 H-0226 입니다.", "냉각 팬 고장(근거 H-0226 · H-0241)."):
            with self.subTest(text=text):
                self.assertEqual((lambda r: (r["ok"], r["reason"]))(_cause(text)), (True, None))

    def test_pattern_edges(self):   # 숫자 · 영문이 더 붙은 것은 ID 아님
        self.assertEqual(report.RECORD.findall("H-0153입니다 · 기록H-0202의 · XH-0001 · H-00012 · R-1234,"), ["H-0153", "H-0202", "R-1234"])
