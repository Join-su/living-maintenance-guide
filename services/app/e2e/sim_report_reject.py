"""E2E 시험 도구(제품 코드 아님): 파트장 보고서 반려 기록을 시험 DB에 넣는다(S-03 — 반려 API는 S-04 P-11).

api 컨테이너 안에서 실행한다:
  docker compose exec -T api python - <session_id> <사유> < e2e/sim_report_reject.py
그 세션 보고서를 반려로 바꾸고(사유 · 파트장 · 시각), 업무 기록에 report_rejected를 덧붙인 뒤
세션 · 알람을 진행 중으로 되돌리고 다음 단계를 다시 연다(FLOW-06: 반려 → 재개 · 수정 → 다시 확정).
"""

import json
import sys
from datetime import UTC, datetime

from sqlalchemy import text

from c1a15.api import deps
from c1a15.api import sessions as svc

sid, reason = int(sys.argv[1]), sys.argv[2]
by = "lead.park"
with deps.engine().begin() as c:
    rejection = {"reason": reason, "by": by, "at": datetime.now(UTC).isoformat()}
    c.execute(
        text(
            "update ops.reports set status = 'rejected', rejection = cast(:r as jsonb), updated_at = now(), "
            "change_seq = :q where session_id = :s"
        ),
        {"r": json.dumps(rejection, ensure_ascii=False), "s": sid, "q": deps.next_change_seq(c)},
    )
    c.execute(text("update ops.diag_sessions set status = 'in_progress' where session_id = :s"), {"s": sid})
    c.execute(
        text(
            "update ops.alarms set status = 'in_progress', change_seq = :q "
            "where alarm_id = (select alarm_id from ops.diag_sessions where session_id = :s)"
        ),
        {"s": sid, "q": deps.next_change_seq(c)},
    )
    events = svc.events_of(c, sid)
    svc._store(c, sid, events[-1]["seq"] + 1, "report_rejected", rejection, by)
    svc._open_step(c, sid, svc._row(c, sid), svc.events_of(c, sid), by)
print(f"sim_report_reject: session {sid}")
