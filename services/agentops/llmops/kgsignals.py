"""D-558 · D-560 (dev-2 · llmops-2 · 정답지 32쪽): the deploy side's graph signals read for the version screen — the folder of
kg-apply/status@1 files (LLMOPS_KG_SIGNAL_DIR, read only; orchestration agents/deploy handoff/d554 gate-signal.md 3절), one
'<report_id>.json' per approval holding its last state. The screen gets the graph versions (the graph the first signal ran on, each
applied new graph — the last one 운영 —, and a row per approval that did not reach the graph) and the approvals a person is told
about (held · failed). 'checking' is the line '새 예외 → 관문 시험 시작' (D-570), 'last' what became of the last one once no gate runs (D-579); 'same' shows nothing. A file is only read whole (the deploy side writes a temporary file and
renames it); one that does not parse or is not this format is left out."""
import json
from pathlib import Path

FORMAT = "kg-apply/status@1"
CHECKS = ("extract_match", "impact", "no_break")
REASON = {"no_break": "기존 시험 유지 안 됨", "extract_match": "추출 안 맞음", "impact": "간섭 범위", "gate_error": "관문 못 돎",
          "revision_mismatch": "Pack 다름", "not_split": "나누지 못함", "overlap": "겹침"}   # 정답지 35쪽 · gate-signal 7절 · D-663 S2 간섭 · 유지
STATE = {"held": "반영 안 됨", "failed": "멈춤"}
OFF_WHY = "꺼짐 — 기존 시험이 안 깨지게 좁힐 조건이 없음"   # D-633: kggate WHY["off"] said as what is missing


def _schema(sigs):
    """D-633 (줄인 안 · llmops-2 견적 204 ④ ⑤): the schema places the signal already carries — a held not_split and the gate's hold
    (a held no_break · a relation the gate turned off, held or applied) — one {report_id, place, lack} each, in signal order.
    Other holds (gate_error · overlap · revision_mismatch …) are not about the schema and are left out."""
    out = []
    for d in sigs:
        rid = d["report_id"]
        for f in d.get("fixed") if isinstance(d.get("fixed"), list) else []:
            if isinstance(f, dict) and f.get("change") == "off":
                out.append({"report_id": rid, "place": "관문 끔", "lack": f"관계 {f.get('relation_id') if f.get('relation_id') not in (None, '') else '—'} {OFF_WHY}"})
        if d["status"] != "held":
            continue
        if d.get("reason") == "not_split":
            out.append({"report_id": rid, "place": "나누지 못함", "lack": "예외를 점검 · 조치로 나누지 못함 — 점검 또는 조치가 비었음"})
        elif d.get("reason") == "no_break":
            nb = (d.get("checks") or {}).get("no_break") if isinstance(d.get("checks"), dict) else None
            broken = [str(x) for x in (nb.get("broken") if isinstance(nb, dict) and isinstance(nb.get("broken"), list) else [])]
            cases = ", ".join(broken[:3]) + (f" 외 {len(broken) - 3}건" if len(broken) > 3 else "")
            fixes = max((d.get("rounds") if isinstance(d.get("rounds"), int) else 0) - 1, 0)
            out.append({"report_id": rid, "place": "관문 보류", "lack": f"기존 시험 {cases + ' ' if cases else ''}유지 안 됨 — "
                        + (f"고침 {fixes}번 뒤에도 좁히지 못함" if fixes else "관계를 좁히지 않고 보류")})
    return out


def _signals(folder):
    out = []
    for p in sorted(Path(folder).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("format") == FORMAT and d.get("report_id") and d.get("status"):
            out.append(d)
    return sorted(out, key=lambda d: str(d.get("at") or ""))


def _checks(d):
    c = d.get("checks")
    if not isinstance(c, dict) or not any(isinstance(c.get(k), dict) for k in CHECKS):
        return None   # the gate never judged (gate_error · not_split): no marks
    return {k: (c.get(k) or {}).get("pass") if isinstance(c.get(k), dict) else None for k in CHECKS}


def no_gate(d):
    """D-637 F7: an applied approval with no new exception — the deploy side ran no gate (rounds 0 · checks {}; gate-signal 92 · 124)."""
    return d.get("status") == "applied" and not (isinstance(d.get("rounds"), int) and d["rounds"] > 0) and _checks(d) is None


def signal(folder, report_id):
    """The last state of one report (None when there is none)."""
    return next((d for d in _signals(folder) if d["report_id"] == report_id), None) if folder and Path(folder).is_dir() else None


def pairs(d):
    """The gate's overlap pairs of a held signal (kggate.overlap — each side's whole rows under 'line'), [] when not an overlap."""
    ov = (d.get("checks") or {}).get("overlap") if d.get("reason") == "overlap" and isinstance(d.get("checks"), dict) else None
    return [p for p in (ov or {}).get("pairs") or [] if isinstance(p, dict)] if isinstance(ov, dict) else []


def _shown(p):
    return {**p, **{k: {kk: vv for kk, vv in (p.get(k) or {}).items() if kk != "line"} for k in ("from_section", "to_section")}}


def read(folder, choices=None):
    """{"versions": [{version, state, checks, at}], "held": [{report_id, record_id, state, graph, rounds, fixes, reason, reason_word,
    checks, at}]} — both empty when there is no folder or no signal. D-560 겹침: a held overlap also gives its pairs (the two
    exceptions side by side · the count) and the person's choice so far (choices: kgcand.choices). D-633: 'schema' — only when there
    is one — the schema places to adjust (_schema)."""
    if not folder or not Path(folder).is_dir():
        return {"versions": [], "held": []}
    every = _signals(folder)
    sigs = [d for d in every if d["status"] in ("applied", "held", "failed")]
    # D-570 (시작 장치 = 로그): the deploy side saw a new exception and started its gate — said on the screen until the file moves on
    going = {"checking": [{"report_id": d["report_id"], "graph": (d.get("graph") or {}).get("running_tag"), "at": d.get("at")}
                          for d in every if d["status"] == "checking"]} if any(d["status"] == "checking" for d in every) else {}
    if not sigs:
        return {"versions": [], "held": [], **going}
    applied = [d for d in sigs if d["status"] == "applied" and (d.get("graph") or {}).get("new_tag")]
    first = (sigs[0].get("graph") or {}).get("running_tag")
    versions = [{"version": first, "state": "이전" if applied else "운영", "checks": None, "at": None}] if first else []
    held = []
    for d in sigs:
        g = d.get("graph") or {}
        if d["status"] == "applied" and g.get("new_tag"):
            versions.append({"version": g["new_tag"], "state": "운영" if d is applied[-1] else "이전", "checks": _checks(d), "at": d.get("at"),
                             **({"no_gate": True} if no_gate(d) else {})})
            continue
        state, reason = STATE[d["status"]], d.get("reason")
        if d["status"] == "held":
            versions.append({"version": None, "state": state, "checks": _checks(d), "at": d.get("at")})
        rounds = d.get("rounds") if isinstance(d.get("rounds"), int) else 0
        held.append({"report_id": d["report_id"], "record_id": d.get("record_id"), "state": state, "graph": g.get("running_tag"), "rounds": rounds,
                     "fixes": max(rounds - 1, 0), "reason": reason,
                     "reason_word": REASON.get(reason) or (f"멈춤 — {reason}" if d["status"] == "failed" and reason else reason or "—"),
                     "checks": _checks(d), "at": d.get("at")})
        if d["status"] == "held" and pairs(d):
            held[-1].update(overlap=[_shown(p) for p in pairs(d)], choice=(choices or {}).get(d["report_id"]))
    # D-579: the gate takes seconds — after it the same place keeps what became of the last new exception, until the next one comes
    d = sigs[-1]
    last = {} if going else {"last": {"report_id": d["report_id"], "graph": (d.get("graph") or {}).get("new_tag") if d["status"] == "applied" else None, "at": d.get("at"),
                                      **({"no_gate": True} if no_gate(d) else {})}}   # D-637 F7: no new exception — said so, not '관문 통과'
    schema = _schema(sigs)
    return {"versions": versions, "held": held, **going, **last, **({"schema": schema} if schema else {})}
