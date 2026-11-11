"""시연 이력 줄(D-570 · recent-condition.md 4절): '그 설비 N일 전 그 절 정비' 이력 한 건을
앱 정비 이력 표에 넣고 · 보이고 · 뺀다.

- 시각은 지금(또는 --at) 기준 며칠 전으로 코드가 정한다. 절 이름은 매뉴얼 표에서(지어내기 0).
- 넣은 줄 = source generated(표 약속 그대로) · record.demo = "D-570" 표시 — 빼기는 이 표시가 있는 줄만(다른 이력 손 0).
- add는 이력 원본 꼴 한 줄(JSON)을 찍는다 — 배포단이 그래프 적재기 입력에 같은 줄을 넣어 Pack records와 맞춤.
  적재기가 줄마다 읽는 승인 칸 · 센서 일곱도 채움: 센서값 = 같은 설비 실제 이력 값 그대로(이력 없는 설비는 거절).
실행: python -m c1a15.demo_history add       (인자 없음 = M-01 · 3일 전 · 4.3.2 · H-0301)
     python -m c1a15.demo_history remove    (인자 없음 = 그 줄만 뺌)
     python -m c1a15.demo_history add --record H-0302 --equipment M-01 --group CL --days-ago 3 --action 4.3.2
       [--check 4.3]
     python -m c1a15.demo_history list --equipment M-01 [--days 7]
     python -m c1a15.demo_history remove --record H-0301
     python -m c1a15.demo_history precheck-add < 예시줄.jsonl   (D-619 사전 점검 예시 줄 · 표시 D-619)
     python -m c1a15.demo_history precheck-remove             (그 표시 줄만 뺌)
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

DEMO = "D-570"
PRECHECK = "D-619"  # 사전 점검 예시 줄 표시(그래프 Record 표시 칸과 같은 값 · 화면에 안 나감)
# 시연 기본 한 줄(D-570 덧 1 · recent-condition.md 5절 recent_record H-0301): M-01 · 3일 전 · 4.3.2 — 인자 없이
DEFAULT = {"record": "H-0301", "equipment": "M-01", "days_ago": 3, "action": "4.3.2"}


def _parse_at(s: str | None) -> datetime:
    at = datetime.fromisoformat(s) if s else datetime.now(UTC)
    at = at.replace(microsecond=0)  # 이력 자료 · engine _secs는 초까지(소수 초면 시간대를 잘못 읽어 며칠 전이 틀림)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def snapshot_of(c: Connection, equipment: str) -> dict:
    """같은 설비 실제 이력(시연 줄 · 승인 보고서 뺌)의 가장 늦은 센서값."""
    row = c.execute(
        text(
            "select sensor_snapshot from knowledge.history_records where equipment_id = :eq "
            "and source = 'generated' and record->>'demo' is null order by occurred_at desc limit 1"
        ),
        {"eq": equipment},
    ).first()
    if row is None:
        raise ValueError(f"센서값을 가져올 이력이 없는 설비: {equipment}")
    return row[0]


def history_line(
    k,
    groups: dict[str, str],
    record: str,
    equipment: str,
    group: str,
    at: datetime,
    actions: list[str],
    checks: list[str],
    snapshot: dict,
) -> dict:
    """이력 원본 꼴(data/history 줄과 같은 칸) 한 건 — 점검 → 조치 차례 · 결과 = 원인 확정(그 조치 절 원인 이름)."""
    for sec in [*checks, *actions]:
        if sec not in k.sections:
            raise ValueError(f"매뉴얼에 없는 절: {sec}")
    cause = next((c for c in k.causes.get(group, []) if c.action_section in actions), None)
    steps = [
        {
            "at": (at + timedelta(minutes=i * 3)).isoformat(),
            "kind": kind,
            "section": sec,
            "name": k.sections[sec]["title"],
            "result": "이상" if kind == "check" else "완료",
        }
        for i, (kind, sec) in enumerate([*(("check", s) for s in checks), *(("action", s) for s in actions)])
    ]
    names = " · ".join(k.sections[s]["title"] for s in actions)
    closed = (at + timedelta(minutes=len(steps) * 3 + 5)).isoformat()
    return {
        "record_id": record,
        "equipment_id": equipment,
        "occurred_at": at.isoformat(),
        "closed_at": closed,
        "symptom_group": {"code": group, "name": groups[group]},
        "sensor_snapshot": snapshot,
        "steps": steps,
        "outcome": {
            "type": "원인 확정",
            "cause_code": cause.code if cause else None,
            "cause_name": cause.name if cause else None,
        },
        "approval": {"status": "승인", "by": "시연", "at": closed},
        "source": "generated",
        "provenance": f"시연 이력 줄({DEMO})",
        "narrative": f"{equipment} {names} 정비(시연 이력 줄).",
        "demo": DEMO,
    }


def add(c: Connection, line: dict, mark: str = DEMO) -> str:
    """같은 번호가 시연 줄이면 바꿔 넣음 · 시연 줄 아닌 이력과 번호가 겹치면 거절(다른 이력 손 0)."""
    old = c.execute(
        text("select record->>'demo' from knowledge.history_records where record_id = :r"), {"r": line["record_id"]}
    ).first()
    if old is not None and old[0] != mark:
        raise ValueError(f"시연 줄이 아닌 이력과 번호가 겹침: {line['record_id']}")
    c.execute(
        text("delete from knowledge.history_records where record_id = :r and record->>'demo' = :d"),
        {"r": line["record_id"], "d": mark},
    )
    c.execute(
        text(
            "insert into knowledge.history_records(record_id, equipment_id, symptom_group, occurred_at, narrative, "
            "sensor_snapshot, record, source, provenance) values (:r, :eq, :g, :at, :n, cast(:snap as jsonb), "
            "cast(:rec as jsonb), 'generated', :p)"
        ),
        {
            "r": line["record_id"],
            "eq": line["equipment_id"],
            "g": line["symptom_group"]["code"],
            "at": line["occurred_at"],
            "n": line["narrative"],
            "snap": json.dumps(line["sensor_snapshot"], ensure_ascii=False),
            "rec": json.dumps(line, ensure_ascii=False),
            "p": line["provenance"],
        },
    )
    return "replaced" if old is not None else "added"


def remove(c: Connection, record: str) -> int:
    return c.execute(
        text("delete from knowledge.history_records where record_id = :r and record->>'demo' = :d"),
        {"r": record, "d": DEMO},
    ).rowcount


def precheck_add(c: Connection, lines: list[dict]) -> int:
    """예시 줄 파일(이력 원본과 같은 칸)을 표시 D-619로 넣음 — 다시 넣으면 같은 줄."""
    for line in lines:
        add(c, {"provenance": f"사전 점검 예시({PRECHECK})", **line, "demo": PRECHECK}, PRECHECK)
    return len(lines)


def precheck_remove(c: Connection) -> int:
    return c.execute(text("delete from knowledge.history_records where record->>'demo' = :d"), {"d": PRECHECK}).rowcount


def listing(c: Connection, equipment: str, days: int, at: datetime) -> list[dict]:
    rows = c.execute(
        text(
            "select record_id, occurred_at, record->>'demo' as demo, record->'steps' as steps "
            "from knowledge.history_records "
            "where equipment_id = :eq and occurred_at < :at and occurred_at >= :since order by occurred_at desc"
        ),
        {"eq": equipment, "at": at, "since": at - timedelta(days=days)},
    ).all()
    return [
        {
            "record_id": r.record_id,
            "occurred_at": r.occurred_at.isoformat(),
            "days_before": (at - r.occurred_at).days,
            "did": [s["section"] for s in r.steps or [] if s.get("kind") == "action"],
            "demo": r.demo == DEMO,
        }
        for r in rows
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m c1a15.demo_history")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--record", default=DEFAULT["record"])
    a.add_argument("--equipment", default=DEFAULT["equipment"])
    a.add_argument("--group", default="CL")
    a.add_argument("--days-ago", type=int, default=DEFAULT["days_ago"])
    a.add_argument("--action", action="append")
    a.add_argument("--check", action="append", default=[])
    a.add_argument("--at", help="기준 시각(ISO · 없으면 지금)")
    li = sub.add_parser("list")
    li.add_argument("--equipment", default=DEFAULT["equipment"])
    li.add_argument("--days", type=int, default=7)
    li.add_argument("--at")
    r = sub.add_parser("remove")
    r.add_argument("--record", default=DEFAULT["record"])
    sub.add_parser("precheck-add")
    sub.add_parser("precheck-remove")
    args = ap.parse_args(argv)

    from c1a15.api import deps

    with deps.engine().begin() as c:
        if args.cmd == "add":
            at = _parse_at(args.at) - timedelta(days=args.days_ago)
            args.action = args.action or [DEFAULT["action"]]
            line = history_line(
                deps.manual(),
                deps.group_names(),
                args.record,
                args.equipment,
                args.group,
                at,
                args.action,
                args.check,
                snapshot_of(c, args.equipment),
            )
            print(
                f"{add(c, line)} {args.record} · {args.equipment} · {at.isoformat()} · 조치 {', '.join(args.action)}",
                file=sys.stderr,
            )
            print(json.dumps(line, ensure_ascii=False))  # 배포단 그래프 적재기 입력에 같은 줄
        elif args.cmd == "precheck-add":
            lines = [json.loads(x) for x in sys.stdin.read().splitlines() if x.strip()]
            print(f"precheck added {precheck_add(c, lines)}", file=sys.stderr)
        elif args.cmd == "precheck-remove":
            print(f"precheck removed {precheck_remove(c)}", file=sys.stderr)
        elif args.cmd == "list":
            for row in listing(c, args.equipment, args.days, _parse_at(args.at)):
                print(json.dumps(row, ensure_ascii=False))
        else:
            n = remove(c, args.record)
            print(f"removed {n} {args.record}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
