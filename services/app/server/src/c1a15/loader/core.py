"""적재기 순수 함수(FLOW-11). 입력은 data/ 아래 매뉴얼 · 이력뿐이다. eval · generation 폴더는 열지 않는다(AC-15)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher

TECH_ACCOUNTS = [("tech.kim", "김 정비"), ("tech.lee", "이 정비"), ("tech.choi", "최 정비"), ("tech.jung", "정 정비")]
LEAD_ACCOUNTS = [("lead.park", "박 파트장")]
COMMON_PRECHECK_SOURCES = ("2.5", "5.5", "6.1")
COMMON_PRECHECK_KEYS = ("이물질", "커버", "작업자")  # 주변 정리 · 커버 · 주변 작업자(공통 시작 전 확인 목록)


@dataclass(frozen=True)
class AccountSeed:
    user_id: str
    display_name: str
    role: str
    password_hash: str


def read_manual(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "manuals" / "manual_sections.json").read_text(encoding="utf-8"))


def read_history(data_dir: Path) -> list[dict]:
    lines = (data_dir / "history" / "maintenance_history.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def grade_rows(sections: list[dict]) -> list[dict]:
    """초기 등급표(부록 B): 등급이 있는 모든 절."""
    return [{"section_id": s["id"], "grade": s["grade"], "minutes": s["minutes"]} for s in sections if s.get("grade")]


def section_checklist(text: str) -> list[str]:
    match = re.search(r"\*\*시작 전 체크리스트\*\*\s*\n((?:\s*- \[ \] .+\n?)+)", text)
    if not match:
        return []
    return [line.split("] ", 1)[1].strip() for line in match.group(1).splitlines() if "] " in line]


def common_precheck_items(sections: list[dict]) -> list[dict]:
    """2.5 · 5.5 · 6.1의 체크리스트에서 주변 정리 · 커버 · 주변 작업자 항목을 순서대로 하나씩 고른다."""
    by_id = {s["id"]: s for s in sections}
    items = []
    for seq, key in enumerate(COMMON_PRECHECK_KEYS, start=1):
        for source in COMMON_PRECHECK_SOURCES:
            hit = next((line for line in section_checklist(by_id[source]["text"]) if key in line), None)
            if hit:
                items.append({"seq": seq, "text": hit, "source_section": source})
                break
        else:
            raise ValueError(f"공통 시작 전 확인 항목을 찾지 못함: {key}")
    return items


def account_seeds(tech_password: str, lead_password: str, hasher: PasswordHasher | None = None) -> list[AccountSeed]:
    if not tech_password or not lead_password:
        raise ValueError("계정 비밀번호는 환경 변수로 받아야 한다(C1A15_TECH_PASSWORD · C1A15_LEAD_PASSWORD)")
    hasher = hasher or PasswordHasher()
    seeds = [AccountSeed(uid, name, "tech", hasher.hash(tech_password)) for uid, name in TECH_ACCOUNTS]
    seeds += [AccountSeed(uid, name, "lead", hasher.hash(lead_password)) for uid, name in LEAD_ACCOUNTS]
    return seeds


def equipment_assignments(history: list[dict]) -> list[dict]:
    """설비 담당표: 이력에 나오는 설비를 번호순으로 정비자 계정에 차례로 배정한다."""
    equipment = sorted({h["equipment_id"] for h in history})
    techs = [uid for uid, _ in TECH_ACCOUNTS]
    return [{"equipment_id": eq, "user_id": techs[i % len(techs)]} for i, eq in enumerate(equipment)]


def history_rows(history: list[dict]) -> list[dict]:
    return [
        {
            "record_id": h["record_id"],
            "equipment_id": h["equipment_id"],
            "symptom_group": h["symptom_group"]["code"],
            "occurred_at": h["occurred_at"],
            "narrative": h["narrative"],
            "sensor_snapshot": h["sensor_snapshot"],
            "record": h,
            "source": "generated",
            "provenance": h["provenance"],
        }
        for h in history
    ]
