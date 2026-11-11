"""D-619 사전 점검 예시 이력 25줄 — 처음 이력의 줄을 본으로 설비 · 시각 · 번호만 바꿈. 앱 · 그래프 모두 이 줄을 받음(세는 곳 = 앱 서버 · 표시 칸 demo = D-619).
패턴 A 흡기 필터 교체(4.3.2) → 7일 안 필터 덮개 문제: 이어짐 둘(남은 하나는 시연 M-01 H-0301 + 승인 보고서) · 이상 없음 둘.
패턴 B 공구 교체(2.4.2) → 14일 안 교체 기록 대조(2.3) 이상 · 패턴 C 엔코더 커넥터 재체결(3.4.2) → 7일 안 회전 조건 설정 확인(3.3) 이상: 이어짐 셋 · 이상 없음 둘.
패턴마다 지금 설비 줄 하나 — 후보 설비 M-04(앞날 이력 없음). 시연 네 기기(M-01 · M-02 · M-03 · M-13)에는 넣지 않음.
원인 칸은 비움: 그래프 DB 그림(HAD · DID · CHECKED · NEXT)에는 들고 release · Pack 판정에는 안 듦.
시각은 --at 기준이라 시연 첫 상태를 만들 때 돌림. 앞 조치 뒤 며칠 안에 처음 이력 줄이 없는 때를 골라 NEXT 선이 바로 이어짐.
패턴 F(D-656 메인 시나리오 · 부를 때만): 컨트롤러 펌웨어 업데이트 → 7일 안 냉각 설정값 확인(4.6) 이상. 매뉴얼에 펌웨어 절이 없어 가장 가까운
기존 항목 7.1 전기 계통 작업(전기 담당)으로 적음(지어낸 절 0) — 펌웨어 줄 = 잠금 · 7.1 조치 · 재가동만 · 이력 글 '컨트롤러 펌웨어 업데이트'.
이어짐 둘(M-05 · M-06) · 이상 없음 둘(M-07 · M-08) = 2/4 · 지금 줄 M-02(5일 전 · 시연 승인으로 셋째 이어짐) · M-04(3일 전 · 후보).
  precheck_examples.py --base <처음 이력> --at <시연 기준 시각 ISO> --out <jsonl> [--patterns A]   # 시연 첫 상태 = A만(D-642 E) · D-656 = C,F"""
import argparse
import copy
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

DEMO = "D-619"
PROVENANCE = "사전 점검 예시"
COVER_TEXT = "필터 덮개 고정 볼트가 풀려 있었음 · 다시 조임"
PLAN = (   # 패턴, 앞 절, 앞 원인, 뒤 원인(None = 원인 미상 + 예외 글), 며칠, 이어짐 설비, 이상 없음 설비, 지금 설비 며칠 전
    ("A", "4.3.2", "CL-1", None, 7, ("M-05", "M-06"), ("M-07", "M-08"), 2),
    ("B", "2.4.2", "TW-1", "TW-3", 14, ("M-09", "M-10", "M-11"), ("M-12", "M-14"), 4),
    ("C", "3.4.2", "PW-3", "PW-1", 7, ("M-15", "M-16", "M-17"), ("M-18", "M-19"), 3),
)
NOW_EQUIPMENT = "M-04"
FIRMWARE = ("F", "7.1", "PW-2", "CL-4", 7, ("M-05", "M-06"), ("M-07", "M-08"), (("M-02", 5), ("M-04", 3)))   # D-656 · 기본 셋에 안 듦
FIRMWARE_TEXT = "컨트롤러 펌웨어 업데이트 — 전기 담당 작업(7.1 전기 계통 작업). 재가동 확인"
FIRMWARE_KEEP = ("lock", "action", "restart")


def _approved(rows):
    return [r for r in rows if r["approval"]["status"] in ("승인", "수정 후 승인")]


def _sample(rows, cause, section=None):
    """본 줄: 그 원인으로 끝난 승인 이력(앞 절이 있으면 그 조치가 든 것) 중 번호가 가장 큰 것."""
    return max((r for r in _approved(rows) if r["outcome"].get("cause_code") == cause and (section is None or any(s.get("kind") == "action" and s.get("section") == section for s in r["steps"]))),
               key=lambda r: r["record_id"])


def _unknown(rows, group):
    """본 줄: 그 증상군의 원인 미상 승인 이력 — 승인 예외처럼 그래프 원인 셈 밖."""
    return max((r for r in _approved(rows) if r["symptom_group"]["code"] == group and r["outcome"]["type"] == "원인 미상·숙련자 인계"), key=lambda r: r["record_id"])


def _free(rows, eq, when, days):
    """when부터 days일 안에 그 설비 처음 이력 줄이 없는 때(하루씩 앞으로)."""
    busy = [datetime.fromisoformat(r["occurred_at"]) for r in rows if r["equipment_id"] == eq]
    while any(when - timedelta(days=1) <= b <= when + timedelta(days=days + 1) for b in busy):
        when -= timedelta(days=1)
    return when


def _row(base, rid, eq, when, drop=None, text=None):
    r = copy.deepcopy(base)
    moved = when - datetime.fromisoformat(r["occurred_at"])

    def shift(text):
        return (datetime.fromisoformat(text) + moved).isoformat(timespec="seconds")

    closed = shift(r["closed_at"])
    r.update(record_id=rid, equipment_id=eq, occurred_at=when.isoformat(timespec="seconds"), closed_at=closed, provenance=PROVENANCE, demo=DEMO)
    for s in r["steps"]:
        if s.get("at"):
            s["at"] = shift(s["at"])
    r["approval"]["at"] = closed   # 본 줄 승인 시각이 시연 시각 뒤로 가지 않게
    head = f"{when:%H:%M} {eq} "
    r["narrative"] = head + (f"{r['symptom_group']['name']} 알람 접수. {text}." if text else re.sub(r"^\d\d:\d\d M-\d\d ", "", r["narrative"]))
    r["steps"] = [s for s in r["steps"] if not (drop and s.get("kind") == "action" and s.get("section") == drop)]   # 뒤 이상 줄이 앞 조치로 세어지지 않게
    r["outcome"] = {"type": "원인 미상·숙련자 인계"}   # 원인 확정 0 = release · Pack 판정 재료 밖(그래프 DB 그림과 앱 이력만) — 메인 시연 값 변화 0
    return {**r, **({"exception_record": {"text": text}} if text else {})}


def _firmware(r):
    """패턴 F 앞 줄: 7.1 본 줄에서 잠금 · 7.1 조치 · 재가동만 남기고 이력 글을 펌웨어 업데이트로(예외 칸 0 · 호출 0)."""
    r["steps"] = [s for s in r["steps"] if s.get("kind") in FIRMWARE_KEEP and (s.get("kind") != "action" or s.get("section") == FIRMWARE[1])]
    r["manual_refs"] = [f"MG-MM {s['section']}" for s in r["steps"]]
    r["escalations"] = []
    r["narrative"] = f"{r['occurred_at'][11:16]} {r['equipment_id']} {FIRMWARE_TEXT}."
    return r


def rows(history, at, patterns=None):
    """patterns = 만들 패턴(기본 셋 다) — D-642 E: 시연 첫 상태는 ("A",)만(사용자 R11 · B · C 줄은 안 만듦 · 번호 · 값은 셋 다일 때와 같음)."""
    want = set(patterns or [p[0] for p in PLAN])
    if want - {p[0] for p in (*PLAN, FIRMWARE)}:
        raise ValueError(f"모르는 패턴: {sorted(want - {p[0] for p in (*PLAN, FIRMWARE)})}")
    base = [json.loads(l) for l in Path(history).read_text(encoding="utf-8").splitlines() if l.strip()]
    now = datetime.fromisoformat(at)
    out = []

    def add(*a, **k):
        out.append(_row(a[0], f"H-P{len(out) + 1:03d}", *a[1:], **k))

    for pattern, sec, before, after, days, hit, clean, ago in PLAN:
        first = _sample(base, before, sec)
        group = first["symptom_group"]["code"]
        follow = _sample(base, after) if after else _unknown(base, group)
        for i, eq in enumerate(hit):
            t = _free(base, eq, now - timedelta(days=60 - i * 7), days)
            add(first, eq, t)
            add(follow, eq, t + timedelta(days=days - 2), drop=sec, text=None if after else COVER_TEXT)
        for i, eq in enumerate(clean):
            add(first, eq, _free(base, eq, now - timedelta(days=40 - i * 7), days))
        add(first, NOW_EQUIPMENT, now - timedelta(days=ago))
        if pattern not in want:   # 번호를 셋 다일 때와 같게 두려고 만든 뒤 뺌
            out[-(len(hit) * 2 + len(clean) + 1):] = [None] * (len(hit) * 2 + len(clean) + 1)
    if FIRMWARE[0] in want:   # D-656: A · B · C 25줄 뒤 번호(H-P026~)
        _, sec, before, after, days, hit, clean, nows = FIRMWARE
        first, follow = _sample(base, before, sec), _sample(base, after)
        for i, eq in enumerate(hit):
            t = _free(base, eq, now - timedelta(days=60 - i * 7), days)
            add(first, eq, t); _firmware(out[-1])
            add(follow, eq, t + timedelta(days=days - 2), drop=sec)
        for i, eq in enumerate(clean):
            add(first, eq, _free(base, eq, now - timedelta(days=40 - i * 7), days)); _firmware(out[-1])
        for eq, ago in nows:
            add(first, eq, now - timedelta(days=ago)); _firmware(out[-1])
    return [r for r in out if r]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--at", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--patterns", default="A,B,C", help="만들 패턴(쉼표) — 시연 첫 상태 = A(D-642 E)")
    a = ap.parse_args()
    got = rows(a.base, a.at, a.patterns.split(","))
    Path(a.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in got), encoding="utf-8")
    print(json.dumps({"rows": len(got), "out": a.out}, ensure_ascii=False))
