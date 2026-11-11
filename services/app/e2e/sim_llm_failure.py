"""E2E 시험 도구(제품 코드 아님): 'LLM 실패 시뮬레이션 세션'(Verification Matrix AC-31 L3)을 만든다.

host 앱 쪽 python으로 실행한다(앱 서버와 같은 환경, EV-001 ES-16): server/.venv/bin/python - <session_id> <last|all> < e2e/sim_llm_failure.py
업무 기록의 지시문 기록(instruction_shown)을 서버 함수와 같은 '원문 대체' 값으로 바꾼다(provider 실패와 같은 결과).
all이면 미리보기 문장 · 메모 해석 기록도 실패로 표시해 같은 세션 3회 연속 실패(숙련자 인계 권함)를 만든다.
"""

import json
import sys

from sqlalchemy import text

from c1a15.api import deps, screen
from c1a15.llm.grounding import FALLBACK_LABEL

sid, mode = int(sys.argv[1]), sys.argv[2]
k = deps.manual()
with deps.engine().begin() as c:
    rows = c.execute(
        text("select seq, type, payload from ops.session_events where session_id = :s order by seq"), {"s": sid}
    ).all()
    shown = [r for r in rows if r.type == "instruction_shown"]
    targets = shown[-1:] if mode == "last" else shown
    for r in targets:
        p = dict(r.payload, steps=[], label=FALLBACK_LABEL, llm_fallback=True)
        p["original"] = screen._plain(screen._block(k, p["section"])[0])
        c.execute(
            text("update ops.session_events set payload = cast(:p as jsonb) where session_id = :s and seq = :q"),
            {"p": json.dumps(p, ensure_ascii=False), "s": sid, "q": r.seq},
        )
    if mode == "all":
        for r in rows:
            if r.type in {"preview_shown", "result"} and "llm_fallback" in r.payload:
                p = dict(r.payload, llm_fallback=True)
                c.execute(
                    text("update ops.session_events set payload = cast(:p as jsonb) where session_id = :s and seq = :q"),
                    {"p": json.dumps(p, ensure_ascii=False), "s": sid, "q": r.seq},
                )
print(f"sim_llm_failure: session {sid} {mode} {len(targets)}")
