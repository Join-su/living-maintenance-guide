"""시도 키 감사(EV-001 CH-15): `pack_call_failed`를 요청 transaction과 따로 commit해, 되돌림 뒤에도 시도 번호를 센다."""

from sqlalchemy import func, select

from c1a15.api import deps
from c1a15.db import models as m

ACTION = "pack_call_failed"


class DbLedger:
    def retryable_stored(self, base_key: str) -> int:
        a = m.AuditEntry
        with deps.engine().connect() as c:
            return c.execute(
                select(func.count()).where(
                    a.action == ACTION,
                    a.detail["base_key"].astext == base_key,
                    a.detail["stored"].as_boolean().is_(True),
                    a.detail["retryable"].as_boolean().is_(True),
                )
            ).scalar_one()

    def record(self, detail: dict) -> None:
        with deps.engine().begin() as c:
            c.execute(
                m.AuditEntry.__table__.insert().values(
                    actor="system", action=ACTION, target=f"pack:{detail['kind']}", detail=detail
                )
            )
