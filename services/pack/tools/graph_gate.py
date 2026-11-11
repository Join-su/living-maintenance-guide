"""D-554 ① 관문(LLMOps)이 부르는 Pack 길 — 후보 그래프 파일 하나를 지금 그래프와 견줌(꼴 = orchestration agents/c1a15 handoff/d554/fields-gate.md 5절).

ⓐ 바뀐 관계(더함 · 바뀜 · 뺌 — 가리키는 예외 절 줄이 바뀌어도 바뀜) ⓑ 영향 범위(판정 함수 exception.match만 · 호출 0):
과거 알람 = 후보 그래프 records(이력의 증상군 · 센서 · 설비) · 시험 case = suite 입력(start 요청) 중 맞는 예외 관계가 달라지는 건수 ·
ⓒ 같은 case를 두 그래프로 실제 Pack Run(Pack 사본에 그래프 파일만 바꿈 · 선언 옵션 그대로) — 결과 칸 그대로 · 정답 파일은 읽지 않음
('망가짐'은 LLMOps가 expectations로 셈). 두 걸음 case(then · LLMOps EV-079와 같은 규칙: 첫 입력 → drop → set → take = 첫 출력 길)는
첫 Run이 끝났을 때만 둘째 Run을 돌리고 base · cand = 둘째 출력(LLMOps final과 같음) · first = 첫 출력. 차례는 base 모두 → cand 모두
(Runtime 둘을 한꺼번에 두지 않음 · eval 135). 그래프 DB 0 · 망 0.

.venv/bin/python -B tools/graph_gate.py --base pack/templates/graph/current.json --cand <후보 current.json> \\
  --cases pack/evals/c1a15-start-v8b/dataset.jsonl [--cases …] --out <결과.json>
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "pack"
sys.path.insert(0, str(PACK))
from custom import alarm, exception  # noqa: E402 — Pack 판정과 같은 함수
from packrt.ir import revision  # noqa: E402
from packrt.run.engine import Runtime  # noqa: E402

GRAPH = Path("templates/graph/current.json")
CONFIG = "execution-config.yaml"


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def relations(base, cand):
    """더함 · 바뀜 · 뺌 관계 id — 관계 줄이나 그 관계가 가리키는 예외 절 줄이 다르면 바뀜."""
    def rows(g):
        sec = {s["no"]: s for s in g["sections"] if s["kind"] == "예외"}
        return {x["id"]: (x, sec.get(x.get("step"))) for x in g.get("exception_relations") or []}
    a, b = rows(base), rows(cand)
    return {"added": sorted(set(b) - set(a)), "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]), "removed": sorted(set(a) - set(b))}


def _hit(graph, group, sensors, equipment_id):
    ex = exception.match(graph, group, sensors, equipment_id)
    return ex[0]["id"] if ex else None


def impact(base, cand, cases):
    """맞는 예외 관계가 두 그래프에서 달라지는 과거 알람 · 시험 case 건수(그 관계 id별)."""
    by, n = {}, 0
    for r in cand["records"]:
        a, b = _hit(base, r["group"], r["sensors"], r["equipment"]), _hit(cand, r["group"], r["sensors"], r["equipment"])
        if a != b:
            n += 1
            by[b or a] = by.get(b or a, 0) + 1
    ids = []
    for suite, row in cases:
        q = row["input"].get("query") or {}
        if not q.get("sensor_snapshot"):
            continue
        s = {**q["sensor_snapshot"], **alarm.derived(q["sensor_snapshot"])}
        g, eq = row["input"]["group"], row["input"].get("equipment_id")
        if _hit(base, g, s, eq) != _hit(cand, g, s, eq):
            ids.append(f"{suite}/{row['case_id']}")
    return {"alarms": {"n": n, "total": len(cand["records"]), "by_relation": dict(sorted(by.items()))}, "cases": {"n": len(ids), "total": len(cases), "ids": ids}}


def _copy(graph_path, d):
    """Pack 사본 — 그래프 파일만 바꾸고 실행 설정의 상대 경로(python · release_roots)는 원래 Pack 자리 기준 절대 경로로."""
    pk = Path(d) / "pack"
    shutil.copytree(PACK, pk, ignore=shutil.ignore_patterns("__pycache__", "evals"))
    shutil.copy(graph_path, pk / GRAPH)
    text = (PACK / CONFIG).read_text(encoding="utf-8")
    text = re.sub(r"python: ([^}\s]+)", lambda m: f"python: {(PACK / m.group(1)).resolve()}", text, count=1)
    text = re.sub(r"release_roots: \[([^\]]*)\]", lambda m: "release_roots: [" + ", ".join(str((PACK / x.strip()).resolve()) for x in m.group(1).split(",")) + "]", text, count=1)
    (pk / CONFIG).write_text(text, encoding="utf-8")
    return pk


def _get(value, path):
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _set(value, path, v):
    *head, last = path.split(".")
    for part in head:
        if not isinstance(value.get(part), dict):
            value[part] = {}
        value = value[part]
    value[last] = v


def next_input(first_input, then, first_output):
    """두 걸음 case의 둘째 입력 — LLMOps runs.next_input과 같은 규칙(첫 입력 사본 · drop 길 뺌 · set 값 넣음 · take 길 = 첫 출력의 그 길 값, 없으면 null)."""
    out = json.loads(json.dumps(first_input))
    for p in then.get("drop") or []:
        *head, last = p.split(".")
        cur = _get(out, ".".join(head)) if head else out
        if isinstance(cur, dict):
            cur.pop(last, None)
    for p, v in (then.get("set") or {}).items():
        _set(out, p, json.loads(json.dumps(v)))
    for p, src in (then.get("take") or {}).items():
        _set(out, p, _get(first_output, src))
    return out


def _one(rt, inp):
    o = rt.run(inp)
    return (True, o.get("output")) if o["status"] == "succeeded" else (False, {"status": o["status"], "error": (o.get("error") or {}).get("code")})


def outputs(graph_path, cases):
    """같은 case를 그 그래프의 Pack 사본으로 Run — {(suite, case_id): {final, first?}} · 그 사본 revision. 두 걸음 case는 첫 Run이 끝났을 때만 둘째 Run."""
    with tempfile.TemporaryDirectory() as d:
        pk = _copy(graph_path, d)
        rt = Runtime(pk, config_name=CONFIG)
        out = {}
        for suite, row in cases:
            ok, first = _one(rt, row["input"])
            if row.get("then") and ok:
                out[(suite, row["case_id"])] = {"final": _one(rt, next_input(row["input"], row["then"], first))[1], "first": first}
            else:
                out[(suite, row["case_id"])] = {"final": first, **({"first": first} if row.get("then") else {})}
        return out, revision.compute(pk)[0]


def gate(base_path, cand_path, case_files):
    base, cand = _read(base_path), _read(cand_path)
    cases = [(Path(f).parent.name, json.loads(line)) for f in case_files for line in Path(f).read_text(encoding="utf-8").splitlines() if line.strip()]
    res = {"relations": relations(base, cand), "impact": impact(base, cand, cases)}   # 꼴 틀린 관계 → exception.RuleError(사본 Run 전에 멈춤)
    a, ra = outputs(base_path, cases)   # base 모두 끝낸 뒤 cand(사본 · Runtime은 한 번에 하나)
    b, rb = outputs(cand_path, cases)
    return {"base": {"layer": base["layer"], "revision": ra}, "cand": {"layer": cand["layer"], "revision": rb}, **res,
            "cases": [{"suite": s, "case_id": c, "same": a[(s, c)] == b[(s, c)], "base": a[(s, c)]["final"], "cand": b[(s, c)]["final"],
                       **({"first": {"base": a[(s, c)]["first"], "cand": b[(s, c)]["first"]}} if "first" in a[(s, c)] else {})} for s, c in a]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--cand", required=True)
    ap.add_argument("--cases", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        res = gate(a.base, a.cand, a.cases)
    except exception.RuleError as e:   # 꼴 틀린 후보 그래프 = 관문 실패(까닭 한 줄)
        print(f"후보 그래프 거절: {e}", file=sys.stderr)
        return 2
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"relations": res["relations"], "impact": {k: v["n"] for k, v in res["impact"].items()}, "same": sum(c["same"] for c in res["cases"]), "cases": len(res["cases"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
