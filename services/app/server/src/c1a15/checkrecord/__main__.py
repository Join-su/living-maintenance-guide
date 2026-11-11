"""체크 기록 CLI(EV-006 CI-4): 승인된 보고서 하나 → 체크 기록 v1을 DB에서 다시 만든다.

실행: python -m c1a15.checkrecord --report <번호>          — 찍기(메모 원문 포함 · 앱 안에서만)
     python -m c1a15.checkrecord --report <번호> --send   — 배포 서버로 다시 보내기(같은 record_key · 메모 뺀 문서)
     python -m c1a15.checkrecord --schema                 — 정본 JSON schema 글
평가 모드 보고서는 만들지 않는다('평가 모드' 한 줄 · 종료 0). C1A15_CHECKS와 상관없이 찍는다.
"""

import argparse
import json
import sys

from c1a15.api import deps

from . import build, send
from .model import schema_text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m c1a15.checkrecord")
    p.add_argument("--report", type=int)
    p.add_argument("--send", action="store_true")
    p.add_argument("--schema", action="store_true")
    a = p.parse_args(argv)
    if a.schema:
        sys.stdout.write(schema_text())
        return 0
    if a.report is None:
        p.error("--report 또는 --schema")
    with deps.engine().connect() as c:
        try:
            doc = build(c, a.report)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
    if doc is None:
        print(f"평가 모드 — 보고서 {a.report} 체크 기록 없음")
        return 0
    if a.send:
        res = send(doc)
        print(json.dumps({"record_key": doc["record_key"], **res}, ensure_ascii=False))
        return 0 if res["status"] in (200, 201) else 1
    print(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
