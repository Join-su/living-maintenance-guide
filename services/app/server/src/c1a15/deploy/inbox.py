"""R12 받은 편지(EV-001 ES-11 · CH-23): 승인 보고서 하나 = 배포 서버 받은 편지 파일 하나.

모양 = deploy 102(API 계약 v0.10 11-1) · `metadata` 칸 = c1a15-pack 012-02(D-287).
앱 업무 기록 칸(작성자 · 승인자 이름 등)은 넣지 않는다.
읽는 쪽 = deploy `r12-update`(센서 하나 빠짐 · 문자 · doc ≠ record_id면 받지 않음, deploy 107)
— 그래서 쓰기 전에 같은 조건으로 거른다.
"""

import json
import os
from pathlib import Path

from c1a15 import redact

SENSORS7 = (  # 원값 5칸 + 알람 Run sensor_derived 2칸(012-04) — 리트리버 재정렬 칸
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
    "temp_diff_k",
    "power_w",
)


def inbox_id(report_id: int) -> str:
    return f"RPT-{report_id:06d}"


def document(rec: dict, approved_at: str) -> dict | None:
    """승인 이력 한 건(lead.history_record) → 받은 편지 문서. 센서 7칸이 다 숫자가 아니면 None(건너뜀)."""
    snap = rec["sensor_snapshot"]
    sensors = {k: snap.get(k) for k in SENSORS7}
    if any(isinstance(v, bool) or not isinstance(v, int | float) for v in sensors.values()):
        return None
    metadata = {
        "evidence_type": "history",
        "source": "approved_report",
        "symptom_group": rec["symptom_group"]["code"],
        **sensors,
        "cause": rec["outcome"]["cause_code"],  # 원인 미상 · 감시 = null
    }
    return {
        "report_id": inbox_id(rec["source"]["report_id"]),
        "approved_at": approved_at,
        "document": {
            "doc": rec["record_id"],
            "record_id": rec["record_id"],
            "text": redact.redact(rec["narrative"])[0],  # 소견 줄 속 이름(001-63) — 이력 DB · 임베딩으로 나감
            "metadata": metadata,
        },
    }


KG_DROP = ("approval", "narrative", "provenance", "source")  # 승인 · 작성 기록 칸(사람 이름 · 앱 업무 기록)


def kg_document(rec: dict, approved_at: str) -> tuple[dict, int]:
    """그래프 적재기 받은 편지(D-543 · c1a15-pack inbox-fit — 초안 그대로 · 바꿀 칸 0): 승인 이력 한 건 →
    {kind kg_record, schema 1, report_id, approved_at, record}. 승인 · 작성 칸과 단계 by(이름)는 빼고,
    글 칸 속 이름은 R12와 같이 거름 → (문서, 거른 수). 일반 · 예외 기록 같은 꼴(예외면 record.exception_record)."""
    record = {k: v for k, v in rec.items() if k not in KG_DROP}
    record["steps"] = [{k: v for k, v in s.items() if k != "by"} for s in rec.get("steps") or []]
    record, n = redact.redact_obj(record)
    doc = {
        "kind": "kg_record",
        "schema": 1,
        "report_id": inbox_id(rec["source"]["report_id"]),
        "approved_at": approved_at,
        "record": record,
    }
    return doc, n


def write(folder: str, doc: dict) -> str:
    """`<폴더>/<report_id>.json` — `.tmp`로 쓴 뒤 이름 바꿈(반쯤 쓴 파일을 읽지 않게). 이미 있으면 → 'exists'."""
    final = Path(folder) / f"{doc['report_id']}.json"
    if final.exists():
        return "exists"
    tmp = final.with_name(final.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, final)  # 있으면 FileExistsError — 덮지 않는 이름 바꿈
    except FileExistsError:
        return "exists"
    finally:
        tmp.unlink(missing_ok=True)
    return "written"
