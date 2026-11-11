"""D-647 B 예외 나누기 평가 묶음 짝(c1a15-split-work · c1a15-split-confirm) 입력 만들기 — Pack 밖 개발 도구 · 호출 0.

  .venv/bin/python -B tools/make_split_suite.py --history <앱 data/history/maintenance_history.jsonl>
- 사례 = 승인된 정비 이력의 원문 글(narrative 그대로) → 약속 v10 kind exception_split 요청(session_id · group · text). 시연 기기 · 시연 줄은 고르지 않음.
- 작업 · 확인 겹침 0(이력 번호 · 기기 · 날짜 모두 다름 아님 — 이력 번호가 다름). 증상군마다 같은 수 · 점검 '이상' + 조치가 있는 글과 조치가 없는 글(나누지 못함 쪽)을 섞음.
- 기대값(expectations.jsonl)은 이 도구가 만들지 않음 — c1a15-eval이 원문에서 정함(지금 모델 출력에 맞추기 0). 칸 정본 = 같은 폴더 README.md.
- 줄의 source 칸(이력 번호 · 기기 · 시각 · 원문에 적힌 점검 · 조치 절 · 같은 기기 최근 30일 정비)은 기대를 정할 때 보는 근거이고 LLMOps는 읽지 않음."""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_EQUIPMENT = {"M-01", "M-02", "M-03", "M-04", "M-13"}   # 시연 기기 · 사전 점검 후보 기기
GROUPS = ("CL", "TW", "PW", "LD")
PLAN = {"work": {"acted": 2, "plain": 1}, "confirm": {"acted": 1, "plain": 1}}   # 증상군마다: 조치 있는 글 · 조치 없는 글
HEAD = """# 평가 suite — Pack revision 밖. D-647 B 예외 나누기(split_exception node · 약속 v10 kind exception_split) {part} 묶음
# 입력 = 승인 정비 이력 원문 글 그대로(tools/make_split_suite.py · 시연 기기 · 시연 줄 0 · 작업 · 확인 겹침 0) · 기대값 = c1a15-eval이 원문에서 정함 · 채점 칸 = README.md
suite: c1a15_split_{part}
split: fixture
graph: run
dataset: dataset.jsonl
expectations: expectations.jsonl
review_policy: {{default: fixture, missing_fixture: stop_before}}
"""


def _at(r):
    return datetime.fromisoformat(r["occurred_at"])


def _sections(r, kind):
    return [s["section"] for s in r["steps"] if s.get("kind") == kind and s.get("section") and not str(s["section"]).startswith("EX-")]


def _recent(rows, r, days=30):
    at = _at(r)
    return [{"record_id": x["record_id"], "days_before": (at - _at(x)).days, "did": _sections(x, "action")}
            for x in sorted(rows, key=_at, reverse=True) if x["equipment_id"] == r["equipment_id"] and at - timedelta(days=days) <= _at(x) < at]


def _ok(r):
    return (r["approval"]["status"] in ("승인", "수정 후 승인") and r["equipment_id"] not in DEMO_EQUIPMENT and r.get("narrative")
            and r.get("provenance") != "사전 점검 예시" and not r.get("demo"))


def pick(rows):
    """{part: [이력 줄]} — 증상군 · 갈래(조치 있음 · 없음)마다 이력 번호순 고르게 띄어 고르고, 작업 · 확인이 번갈아 가짐."""
    out = {"work": [], "confirm": []}
    for g in GROUPS:
        for kind in ("acted", "plain"):
            pool = sorted((r for r in rows if _ok(r) and r["symptom_group"]["code"] == g
                           and bool(_sections(r, "action")) == (kind == "acted")
                           and (kind == "plain" or any(s.get("kind") == "check" and s.get("result") == "이상" for s in r["steps"]))), key=lambda r: r["record_id"])
            need = PLAN["work"][kind] + PLAN["confirm"][kind]
            if len(pool) < need:
                raise SystemExit(f"{g} {kind} 사례 모자람: {len(pool)} < {need}")
            step = len(pool) / need
            chosen = [pool[int(i * step)] for i in range(need)]
            out["work"] += chosen[0::2][:PLAN["work"][kind]] if PLAN["work"][kind] >= PLAN["confirm"][kind] else chosen[1::2][:PLAN["work"][kind]]
            out["confirm"] += [c for c in chosen if c not in out["work"]][:PLAN["confirm"][kind]]
    return out


def line(r, rows, part, n):
    return {"case_id": f"SP{'W' if part == 'work' else 'C'}{n:02d}",
            "input": {"kind": "exception_split", "session_id": f"S-SPLIT-{r['record_id']}", "group": r["symptom_group"]["code"], "text": r["narrative"]},
            "source": {"record_id": r["record_id"], "equipment_id": r["equipment_id"], "occurred_at": r["occurred_at"], "outcome": r["outcome"].get("type"),
                       "checked": _sections(r, "check"), "did": _sections(r, "action"), "recent_maintenance": _recent(rows, r)}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    a = ap.parse_args(argv)
    rows = [json.loads(x) for x in Path(a.history).read_text(encoding="utf-8").splitlines() if x.strip()]
    got = pick(rows)
    assert not {r["record_id"] for r in got["work"]} & {r["record_id"] for r in got["confirm"]}
    for part, rs in got.items():
        d = ROOT / f"pack/evals/c1a15-split-{part}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "suite.yaml").write_text(HEAD.format(part=part), encoding="utf-8")
        (d / "dataset.jsonl").write_text("".join(json.dumps(line(r, rows, part, i), ensure_ascii=False) + "\n" for i, r in enumerate(rs, 1)), encoding="utf-8")
        if not (d / "expectations.jsonl").exists():
            (d / "expectations.jsonl").write_text("", encoding="utf-8")   # c1a15-eval이 채움
        print(json.dumps({"suite": d.name, "cases": len(rs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
