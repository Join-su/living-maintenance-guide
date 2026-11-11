"""suite c1a15-report 만들기(D-290 (2)) — Pack 밖 개발 도구. Pack 코드는 이 도구도 앱도 import하지 않는다.

  .venv/bin/python tools/make_report_suite.py <앱 내보내기 c1a15-report-20.jsonl>
- 입력 = 앱(c1a15-app)이 내보낸 20줄 `{case_id, source, input, app_sentences, key_same}`(시연 10 + 카드 val 10, 이름 · ID · 파트장 답 메모 0).
  `input` = 약속 v3 `kind: report` 요청 본문 그대로, 기대값 = `app_sentences`(앱이 쓴 원인 · 조치)를 원문 대체 없이(attempts 1).
- 앱 내보내기 줄을 바꾸지 않고, 약속 v3 schema · 줄 수 · case_id · key_same을 검사한 뒤 쓴다. 합성 템플릿(옛 make_evals.py 4)은 버렸다."""
import hashlib
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "pack" / "evals" / "c1a15-report"
SUITE = """# 평가 suite — Pack revision 밖. 보고서 8항목 초안 20건(시연 10 + 카드 val 10)의 원인 · 조치 문장 — 기대값 = 앱 report_written 문장(가짜 provider)
# 만든 도구: tools/make_report_suite.py(앱 내보내기 D-290 c1a15-report-20.jsonl sha256 {sha} — test 카드 0 · 이름 · ID · 답 메모 0)
suite: c1a15_report
split: fixture
graph: run
dataset: dataset.jsonl
expectations: expectations.jsonl
review_policy: {{default: fixture, missing_fixture: stop_before}}
"""
FIELDS = ("cause", "action")


def main(src):
    raw = src.read_bytes()
    rows = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
    ids = [r["case_id"] for r in rows]
    assert ids == [f"RD{i:02d}" for i in range(1, 11)] + [f"RV{i:02d}" for i in range(1, 11)], ids
    req = jsonschema.Draft202012Validator(json.loads((ROOT / "contracts/v3/run_request.json").read_text(encoding="utf-8")))
    for r in rows:
        assert r["input"]["kind"] == "report" and req.is_valid(r["input"]), r["case_id"]
        assert r["key_same"] is True and set(r["app_sentences"]) == set(FIELDS), r["case_id"]
    ds = [{"case_id": r["case_id"], "input": r["input"]} for r in rows]
    ex = [{"case_id": r["case_id"],
           "final": [{"path": "report", "equals": {f: {"text": r["app_sentences"][f], "fallback": False, "attempts": 1, "reason": None} for f in FIELDS}}],
           "nodes": {"rewrite_report": [{"reached": False}], "unsupported": [{"reached": False}]}} for r in rows]
    OUT.mkdir(exist_ok=True)
    for name, lines in (("dataset.jsonl", ds), ("expectations.jsonl", ex)):
        (OUT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    (OUT / "suite.yaml").write_text(SUITE.format(sha=hashlib.sha256(raw).hexdigest()), encoding="utf-8")
    print(f"c1a15-report {len(ds)}건 · 앱 내보내기 sha256 {hashlib.sha256(raw).hexdigest()}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
