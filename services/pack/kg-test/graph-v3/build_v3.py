"""D-536 ② 지식그래프 — 관계표 없는 매뉴얼 MG-MM 3.0(절 = 노드) + 승인된 정비 이력 → 그래프 DB 사본(꼬리표 mm3-v2 · 절 노드에 시작 전 체크리스트 칸 — v1은 칸 없음 · 나란히 남김).
점검 ↔ 원인 ↔ 조치 연결은 이력에서만 셈. 매뉴얼에서는 절 · 증상군 · 권한 등급 · 소요 분만 읽음.
번호-대응.csv는 이력의 옛 절 번호를 새 번호로 바꾸는 데만 씀 — 그래프에 넣지 않음(절끼리 잇는 관계 0).
MG-MM 2.0(부록 A 원인–점검 표가 든 정답)은 이 길에서 읽지 않음(test_v3.py가 연 파일을 셈).

kg-test/.venv-kg/bin/python kg-test/graph-v3/build_v3.py --history <앱 저장소>/data/history/maintenance_history.jsonl [--dump]

그래프 꼴:
  (:Group 증상군) −[:HAS_SECTION]→ (:Section 매뉴얼 절 · kind 점검/사전 확인/조치/공통/재가동/마감)      매뉴얼 장 구조
  (:Record 이력 한 건) −[:ALARM_IN]→ (:Group) · −[:CHECKED {result}]→ (:Section) · −[:DID]→ (:Section) · −[:CONFIRMED]→ (:Cause)   원본 층
  (:Section 점검) −[:INDICATES {n}]→ (:Cause)       이상이 나온 점검 → 확정 원인(근거 건수 n)      관계 층(이력에서 셈)
  (:Cause) −[:REMEDIED_BY {n}]→ (:Section 조치)     확정 원인 → 한 조치
  (:Group) −[:CANDIDATE {n}]→ (:Cause)              증상군 → 원인 후보
  (:Layer {version, manual_sha256, history_sha256, map_sha256})"""
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
KG = HERE.parent
sys.path.insert(0, str(KG / "graph"))
from conn import driver  # noqa: E402

VERSION = "mm3-v3"   # v3(D-560): 점검 절 노드에 설명 칸 desc(판정 기준 첫 문장 · LLM 0) · v2(D-543): 시작 전 체크리스트 칸 · v1은 칸 없음 — 옛 꼬리표는 그래프 DB에 나란히 남김
MANUAL = KG / "manual-v3/MG-MM-3.0-관계없음.md"
MAP = KG / "manual-v3/번호-대응.csv"
SENSORS = ("air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min", "temp_diff_k", "power_w")
COVER, ISSUE = "덮개", "필터 덮개 고정"   # D-619 패턴 A 덮개 선(이력 예외 글에 이 말이 있으면 이음)
LABELS = ("Layer", "Group", "Section", "Record", "Cause", "Equipment")   # D-570: 설비 노드 + 설비 → 이력(HAD) · 이력 → 다음 이력(NEXT · 같은 설비 시간순)
BATCH = 500


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def manual_sections(path=MANUAL):
    """새 매뉴얼 → 절 목록(번호 · 이름 · 종류 · 증상군 · 권한 등급 · 소요 분 · 시작 전 체크리스트). 관계 글은 읽지 않음."""
    L = Path(path).read_text(encoding="utf-8").split("\n")
    out, group = [], None
    for i, l in enumerate(L):
        m = re.match(r"^(#{3,4}) (\d+(?:\.\d+)+) (.+)$", l)
        if not m:
            continue
        no, name = m.group(2), m.group(3).strip()
        parts = no.split(".")
        if len(parts) == 2 and parts[0] in ("2", "3"):
            group = re.sub(r"\s*\(.*\)$", "", name)   # '공구 마모 경고 (공구 계통)' → '공구 마모 경고'
            continue
        if parts[0] in ("2", "3") and len(parts) == 3:
            if parts[0] == "2" and parts[2] == "1":
                continue   # 알람 조건과 점검 목록(절 아님)
            kind = "조치" if parts[0] == "3" else ("사전 확인" if parts[2] == "2" else "점검")
            grp = None if no.startswith("3.5.") else group
        elif parts[0] == "1":
            kind, grp = "공통", None
        elif parts[0] == "4":
            kind, grp = ("재가동" if no == "4.1" else "마감"), None
        else:
            continue
        meta = next((x for x in L[i + 1:i + 4] if x.startswith("**권한 등급**")), "")
        g = re.search(r"\*\*권한 등급\*\* ([A-D])", meta)
        mi = re.search(r"\*\*소요\*\* 약 (\d+)분", meta)
        end = next((j for j in range(i + 1, len(L)) if re.match(r"^#{1,4} ", L[j])), len(L))
        body = "\n".join(L[i + 1:end])
        out.append({"no": no, "name": name, "kind": kind, "group": grp, "grade": g.group(1) if g else None, "minutes": int(mi.group(1)) if mi else None,
                    "checklist": checklist(body), **({"desc": d} if kind == "점검" and (d := describe(body)) else {})})
    return out


def describe(text):
    """D-560 점검 설명 = '**판정 기준**' 첫 문장(관찰한 현상이 어느 점검에 속하는지 — 무엇이면 '이상'인지) · LLM 0 · 없으면 None."""
    m = re.search(r"\*\*판정 기준\*\*\s*(.+?다\.)(?=\s|$)", text or "")
    return m.group(1).strip() if m else None


def checklist(text):
    """'**시작 전 체크리스트**' 아래 '- [ ] …' 줄들(B등급 절) — Pack 판정 지식의 Check.checklist와 같은 읽기."""
    m = re.search(r"\*\*시작 전 체크리스트\*\*\s*\n((?:\s*- \[ \] .+\n?)+)", text or "")
    return [x.split("] ", 1)[1].strip() for x in m.group(1).splitlines() if "] " in x] if m else []


def number_map(path=MAP):
    with Path(path).open(encoding="utf-8") as f:
        return {r["옛_절"]: r["새_절"] for r in csv.DictReader(f) if r["종류"] in ("점검", "사전 확인", "조치", "공통", "재가동", "마감")}


def graph_rows(history, manual=MANUAL, mapping=MAP):
    """그래프에 넣을 노드 · 관계(파이썬 값) — 같은 입력이면 같은 목록."""
    secs = manual_sections(manual)
    known = {s["no"] for s in secs}
    nmap = number_map(mapping)
    groups = sorted({s["group"] for s in secs if s["group"]})
    recs = [json.loads(l) for l in Path(history).read_text(encoding="utf-8").splitlines() if l.strip()]
    records, checked, did, confirmed, causes, had_issue = [], [], [], [], {}, []
    ind, rem, cand = Counter(), Counter(), Counter()
    for r in sorted(recs, key=lambda x: x["record_id"]):
        if r["approval"]["status"] not in ("승인", "수정 후 승인"):
            continue
        g = r["symptom_group"]["name"]
        if g not in groups:
            raise ValueError(f"{r['record_id']}: 매뉴얼에 없는 증상군")
        oc = r["outcome"]
        records.append({"id": r["record_id"], "name": r["record_id"], "group": g, "group_code": r["symptom_group"]["code"], "ts": r["occurred_at"],
                        "equipment": r["equipment_id"], "outcome": oc["type"], **{f: r["sensor_snapshot"][f] for f in SENSORS}, **({"demo": r["demo"]} if r.get("demo") else {})})   # D-619 표시 칸(예시 줄 D-619 · 시연 줄 D-570 · 내보내기 밖)
        if COVER in (r.get("exception_record") or {}).get("text", ""):
            had_issue.append({"id": r["record_id"]})
        for i, s in enumerate(r["steps"]):
            if s["kind"] not in ("check", "action") or "section" not in s or str(s["section"]).startswith("EX-"):   # 예외 절(D-546 덧 27 ②)은 번호 바꾸기 · 매뉴얼 절 관계 밖
                continue
            new = nmap.get(s["section"])
            if new is None or new not in known:
                raise ValueError(f"{r['record_id']}: 번호를 바꿀 수 없는 절")
            if s["kind"] == "check":
                checked.append({"id": r["record_id"], "no": new, "result": s["result"], "i": i})
            else:
                did.append({"id": r["record_id"], "no": new, "i": i})
        code = oc.get("cause_code")
        if code:
            causes[code] = oc["cause_name"]
            confirmed.append({"id": r["record_id"], "code": code})
            cand[(g, code)] += 1
            for c in checked:
                if c["id"] == r["record_id"] and c["result"] == "이상":
                    ind[(c["no"], code)] += 1
            for d in did:
                if d["id"] == r["record_id"]:
                    rem[(code, d["no"])] += 1
    line = {}   # D-570 시간 선: 설비마다 이력을 시각순으로(같은 시각이면 이력 번호순)
    for x in sorted(records, key=lambda x: (x["equipment"], datetime.fromisoformat(x["ts"]), x["id"])):
        line.setdefault(x["equipment"], []).append(x)
    nxt = [{"a": a["id"], "b": b["id"], "days": round((datetime.fromisoformat(b["ts"]) - datetime.fromisoformat(a["ts"])).total_seconds() / 86400, 2)}
           for xs in line.values() for a, b in zip(xs, xs[1:])]
    meta = {"version": VERSION, "manual_sha256": _sha(manual), "history_sha256": _sha(history), "map_sha256": _sha(mapping), "records": len(records)}
    return {"meta": meta, "groups": [{"name": g} for g in groups], "sections": secs,
            "has_section": [{"group": s["group"], "no": s["no"]} for s in secs if s["group"]],
            "causes": [{"code": c, "name": n} for c, n in sorted(causes.items())],
            "records": records, "checked": checked, "did": did, "confirmed": confirmed,
            "equipment": [{"id": e} for e in sorted(line)], "had": [{"eq": x["equipment"], "id": x["id"]} for x in records], "next": nxt,
            "issues": [{"name": ISSUE}] if had_issue else [], "had_issue": had_issue,
            "indicates": [{"no": a, "code": b, "n": n} for (a, b), n in sorted(ind.items())],
            "remedied_by": [{"code": a, "no": b, "n": n} for (a, b), n in sorted(rem.items())],
            "candidate": [{"group": a, "code": b, "n": n} for (a, b), n in sorted(cand.items())]}


def clear(s, v):
    s.run("MATCH (n {layer: $v}) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS", v=v).consume()


def load(history, layer=VERSION, drv=None, manual=MANUAL, mapping=MAP):
    return put(graph_rows(history, manual, mapping), layer, drv)


def put(g, layer, drv=None):
    """graph_rows 꼴 값 → 그래프 DB 꼬리표 하나(있던 꼬리표는 지우고 새로)."""
    own = drv is None
    drv = drv or driver()
    q = [
        ("UNWIND $rows AS r CREATE (x:Group {layer: $v}) SET x += r", g["groups"]),
        ("UNWIND $rows AS r CREATE (x:Section {layer: $v}) SET x += r", g["sections"]),
        ("UNWIND $rows AS r CREATE (x:Cause {layer: $v}) SET x += r", g["causes"]),
        ("UNWIND $rows AS r CREATE (x:Record {layer: $v}) SET x += r", g["records"]),
        ("UNWIND $rows AS r MATCH (a:Group {layer: $v, name: r.group}), (b:Section {layer: $v, no: r.no}) CREATE (a)-[:HAS_SECTION]->(b)", g["has_section"]),
        ("UNWIND $rows AS r MATCH (a:Record {layer: $v, id: r.id}), (b:Group {layer: $v, name: r.group}) CREATE (a)-[:ALARM_IN]->(b)", g["records"]),
        ("UNWIND $rows AS r MATCH (a:Record {layer: $v, id: r.id}), (b:Section {layer: $v, no: r.no}) CREATE (a)-[:CHECKED {result: r.result, i: r.i}]->(b)", g["checked"]),
        ("UNWIND $rows AS r MATCH (a:Record {layer: $v, id: r.id}), (b:Section {layer: $v, no: r.no}) CREATE (a)-[:DID {i: r.i}]->(b)", g["did"]),
        ("UNWIND $rows AS r MATCH (a:Record {layer: $v, id: r.id}), (b:Cause {layer: $v, code: r.code}) CREATE (a)-[:CONFIRMED]->(b)", g["confirmed"]),
        ("UNWIND $rows AS r MATCH (a:Section {layer: $v, no: r.no}), (b:Cause {layer: $v, code: r.code}) CREATE (a)-[:INDICATES {n: r.n}]->(b)", g["indicates"]),
        ("UNWIND $rows AS r MATCH (a:Cause {layer: $v, code: r.code}), (b:Section {layer: $v, no: r.no}) CREATE (a)-[:REMEDIED_BY {n: r.n}]->(b)", g["remedied_by"]),
        ("UNWIND $rows AS r MATCH (a:Group {layer: $v, name: r.group}), (b:Cause {layer: $v, code: r.code}) CREATE (a)-[:CANDIDATE {n: r.n}]->(b)", g["candidate"]),
        ("UNWIND $rows AS r CREATE (x:Equipment {layer: $v}) SET x += r", g["equipment"]),
        ("UNWIND $rows AS r MATCH (a:Equipment {layer: $v, id: r.eq}), (b:Record {layer: $v, id: r.id}) CREATE (a)-[:HAD]->(b)", g["had"]),
        ("UNWIND $rows AS r MATCH (a:Record {layer: $v, id: r.a}), (b:Record {layer: $v, id: r.b}) CREATE (a)-[:NEXT {days: r.days}]->(b)", g["next"]),
        ("UNWIND $rows AS r CREATE (x:Issue {layer: $v}) SET x += r", g["issues"]),   # D-619 패턴 A 뒤 사건: 절이 없어 이력 예외 글에 덮개가 든 줄을 한 노드로 묶음
        (f"UNWIND $rows AS r MATCH (a:Record {{layer: $v, id: r.id}}), (b:Issue {{layer: $v, name: '{ISSUE}'}}) CREATE (a)-[:HAD_ISSUE]->(b)", g["had_issue"]),
    ]
    try:
        with drv.session() as s:
            for label, key in (("Section", "no"), ("Record", "id"), ("Group", "name"), ("Cause", "code"), ("Equipment", "id")):
                s.run(f"CREATE INDEX kg3_{label.lower()} IF NOT EXISTS FOR (n:{label}) ON (n.layer, n.{key})").consume()
            clear(s, layer)
            s.run("CREATE (m:Layer {layer: $v}) SET m += $meta", v=layer, meta=g["meta"]).consume()
            for cypher, rows in q:
                for i in range(0, len(rows), BATCH):
                    s.run(cypher, rows=rows[i:i + BATCH], v=layer).consume()
    finally:
        if own:
            drv.close()
    return layer, {k: len(x) for k, x in g.items() if isinstance(x, list)}


def dump(layer, drv):
    with drv.session() as s:
        nodes = s.run("MATCH (n {layer: $v}) RETURN labels(n) AS l, properties(n) AS p", v=layer).data()
        rels = s.run("MATCH (a {layer: $v})-[r]->(b {layer: $v}) RETURN type(r) AS t, properties(r) AS p, labels(a)[0] AS la, "
                     "coalesce(a.id, a.no, a.code, a.name) AS ka, labels(b)[0] AS lb, coalesce(b.id, b.no, b.code, b.name) AS kb", v=layer).data()
    lines = sorted(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in nodes + rels)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), {"nodes": len(nodes), "rels": len(rels)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--layer", default=VERSION)
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    v, counts = load(a.history, a.layer)
    out = {"layer": v, **counts}
    if a.dump:
        with driver() as d:
            out["dump_sha256"], out["dump_counts"] = dump(v, d)
    print(json.dumps(out, ensure_ascii=False))
