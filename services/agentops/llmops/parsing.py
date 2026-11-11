"""Validation-data and input-set file parsing (AC-02, AC-33, AC-38). Messages follow the approved seed (parseFile)."""
import csv
import io
import json


class ParseError(ValueError):
    pass


def _input_value(v):
    """A JSON object input is kept as an object (EV-002 CHG-03, legacy releases included); anything else is text as before."""
    return v if isinstance(v, dict) else str(v)


def _empty(v):
    return v is None or (not isinstance(v, dict) and str(v).strip() == "")


def parse_file(name: str, text: str, need_expected: bool):
    """Return cases: [{input, expected, forbidden}] when need_expected, else [input, ...]. Raises ParseError."""
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    if ext not in ("json", "csv"):
        raise ParseError(f"JSON/CSV 파일만 올릴 수 있습니다 (받은 파일: {name})")
    if ext == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ParseError(f"JSON을 읽을 수 없습니다 — {e.msg}")
        if not isinstance(data, list):
            raise ParseError("JSON은 case 배열이어야 합니다")
        rows = [(f"{i + 1}번째 case", r if isinstance(r, dict) else {"input": r}) for i, r in enumerate(data)]
    else:
        lines = [l for l in text.splitlines() if l.strip()]
        reader = csv.reader(io.StringIO("\n".join(lines)))
        table = list(reader)
        head = [h.strip() for h in (table[0] if table else [])]
        rows = [(f"{i + 2}번째 줄", {h: (v[j].strip() if j < len(v) else "") for j, h in enumerate(head)}) for i, v in enumerate(table[1:])]
    if not rows:
        raise ParseError("case가 하나도 없습니다")
    out = []
    for where, r in rows:
        if _empty(r.get("input")):
            raise ParseError(f"{where}에 '입력(input)' 필드가 없습니다")
        if need_expected and (r.get("expected") is None or str(r.get("expected")).strip() == ""):
            raise ParseError(f"{where}에 '기대 정답(expected)' 필드가 없습니다")
        out.append({"input": _input_value(r["input"]), "expected": str(r["expected"]), "forbidden": str(r.get("forbidden") or "")} if need_expected else _input_value(r["input"]))
    return out
