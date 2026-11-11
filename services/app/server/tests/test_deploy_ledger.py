# ruff: noqa: F811, E501 — test_api의 fixture(db)를 이름으로 가져와 쓴다, 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-06(시험 DB): 시도 키 감사 pack_call_failed — 요청 transaction이 되돌려져도 남고, 그 기본 키의 '저장된 retryable 실패'만 센다."""

import pytest
from sqlalchemy import text

from test_api import db  # noqa: F401

pytestmark = pytest.mark.db


def test_ledger_counts_only_stored_retryable_for_that_base_key_and_survives_rollback(db):
    from c1a15.api import deps
    from c1a15.deploy.ledger import DbLedger

    led = DbLedger()
    base = "c1a15-judge-S-A1-3-0123456789abcdef"
    with pytest.raises(RuntimeError):
        with deps.engine().begin():  # 요청 transaction이 실패로 되돌려지는 모양
            led.record(
                {
                    "kind": "judge",
                    "base_key": base,
                    "key": base,
                    "code": "trace_unavailable",
                    "run_id": "r1",
                    "stored": True,
                    "retryable": True,
                }
            )
            raise RuntimeError("요청 실패 → 되돌림")
    led.record(
        {
            "kind": "judge",
            "base_key": base,
            "key": base + "-r1",
            "code": "deployment_not_registered",
            "run_id": "r2",
            "stored": True,
            "retryable": False,
        }
    )
    led.record(
        {
            "kind": "judge",
            "base_key": base,
            "key": base,
            "code": "timeout",
            "run_id": None,
            "stored": False,
            "retryable": False,
        }
    )
    led.record(
        {
            "kind": "judge",
            "base_key": base + "x",
            "key": base + "x",
            "code": "trace_unavailable",
            "run_id": "r3",
            "stored": True,
            "retryable": True,
        }
    )
    assert (
        led.retryable_stored(base) == 1 and led.retryable_stored(base + "x") == 1 and led.retryable_stored("없음") == 0
    )
    with db.connect() as c:
        rows = c.execute(
            text(
                "select actor, target from ops.audit_log where action = 'pack_call_failed' and detail->>'base_key' = :b"
            ),
            {"b": base},
        ).all()
    assert len(rows) == 3 and {tuple(r) for r in rows} == {("system", "pack:judge")}
