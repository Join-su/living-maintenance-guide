"""E2E 시험 도구(제품 코드 아님): 파트장 호출 답 기록을 시험 DB에 넣는다(S-03 — 파트장 답 API는 S-04 P-11).

host 앱 쪽 python으로 실행한다(앱 서버와 같은 환경 — C1A15_DEPLOY_URL 포함, EV-001 ES-16):
  server/.venv/bin/python - <session_id> <approved|daytime|direct|assign> < e2e/sim_lead_answer.py
그 세션의 마지막 호출(ops.calls)에 답을 적고, 업무 기록에 call_answer를 덧붙인 뒤 다음 단계를 다시 연다
(PBC-4 v10 실행 해석 ③: S-03에서는 시험 DB에 넣은 답 기록으로 화면 · 보고서 반영을 확인한다).
"""

import json
import sys
from datetime import UTC, datetime

from sqlalchemy import text

from c1a15.api import deps
from c1a15.api import sessions as svc

sid, answer = int(sys.argv[1]), sys.argv[2]
by = "lead.park"
with deps.engine().begin() as c:
    call = c.execute(
        text("select call_id, section from ops.calls where session_id = :s order by call_id desc limit 1"), {"s": sid}
    ).first()
    payload = {
        "answer": answer,
        "section": call.section,
        "by": by,
        "at": datetime.now(UTC).isoformat(),
        "note": None,
        "call_id": call.call_id,
    }
    c.execute(
        text("update ops.calls set answer = cast(:a as jsonb), answered_at = now() where call_id = :c"),
        {"a": json.dumps(payload), "c": call.call_id},
    )
    events = svc.events_of(c, sid)
    svc._store(c, sid, events[-1]["seq"] + 1, "call_answer", payload, by)
    svc._open_step(c, sid, svc._row(c, sid), svc.events_of(c, sid), by)
print(f"sim_lead_answer: session {sid} {answer} {call.section}")
