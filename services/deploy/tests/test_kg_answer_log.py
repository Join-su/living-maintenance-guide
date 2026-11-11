"""[dev-2] D-597: kg_apply가 추출기 답을 로그에 한 줄씩 — recent · 근거 기록 · 조건 칸 이름만(문턱 값 · 원문 · 키 0)."""
import json
import types

from test_kg_apply import LETTER, K, exgraph_says, letter  # noqa: F401 - fixture K

ROW = {"id": "EX-R-0020", "records": ["R-0002"], "text_sha256": "abc", "cause_name": "원문 글자",
       "when": {"group": "온도 차 저하", "sensors": [], "recent": {"section": "4.3.2", "days": 7}},
       "context": {"recent_record": "H-0301", "equipment_id": "M-01"}}
SENSOR = {"id": "EX-R-6001", "records": ["R-6001"], "cause_name": "원문 글자", "context": {"sensors": {"temp_diff_k": 7.8}},
          "when": {"group": "온도 차 저하", "equipment_id": "M-01", "sensors": [["temp_diff_k", "<=", 7.8]]}}


def says(K, monkeypatch, tmp_path, rows, notices):  # noqa: F811
    exgraph_says(K, monkeypatch, tmp_path, "")

    def sh(args, **kw):
        out = args[args.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"sections": [], "exception_relations": rows}, f, ensure_ascii=False)
        rep = {"added": [r["id"] for r in rows], "skipped": [], "notices": notices, "usage": None}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(rep), stderr="")

    monkeypatch.setattr(K.A, "sh", sh)


def logged(K, monkeypatch, tmp_path, rows, notices=()):  # noqa: F811
    K.STATE.mkdir(parents=True, exist_ok=True)
    letter(K, "RPT-000002", record={**LETTER["record"], "record_id": "R-0002"})
    says(K, monkeypatch, tmp_path, rows, list(notices))
    K.extract([(None, json.loads((K.INBOX / "RPT-000002.json").read_text()))], "v1")
    return (K.STATE / "kg-apply.log").read_text()


def test_recent_answer_is_logged(K, monkeypatch, tmp_path):  # noqa: F811
    text = logged(K, monkeypatch, tmp_path, [ROW])
    assert "추출기 답 EX-R-0020 · 기록 ['R-0002'] · recent {'section': '4.3.2', 'days': 7} · 정비 기록 H-0301 · 센서 칸 []" in text


def test_sensor_answer_shows_names_only(K, monkeypatch, tmp_path):  # noqa: F811
    text = logged(K, monkeypatch, tmp_path, [SENSOR], [{"record_id": "R-6001", "code": "recent_answer"}])
    assert "추출기 답 EX-R-6001 · 기록 ['R-6001'] · recent None · 정비 기록 None · 센서 칸 ['temp_diff_k']" in text
    assert "알림 [('R-6001', 'recent_answer')]" in text
    assert "7.8" not in text and "원문 글자" not in text
