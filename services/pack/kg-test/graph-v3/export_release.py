"""D-536 ④ 그래프 release 내보내기 — 그래프 DB 꼬리표를 Pack이 읽는 파일 하나로(pack/templates/graph/current.json · D-545 자리 · 꼴 = handoff/d545/graph-shape.md).
Pack custom node는 망 0 · 표준 라이브러리만이라 그래프 DB에 붙지 않음 → 벡터 DB release처럼 '만든 값'을 파일로 받아 읽음(바꿔 끼움 = node의 layer 줄).
- 관계(INDICATES · REMEDIED_BY · CANDIDATE)와 근거 건수는 그래프에서 그대로 읽음.
- 선별 · 확정 역할: 같은 원인에 이상 나온 점검이 둘 이상이면, 둘 다 이상이던 이력에서 먼저 한 점검 = 선별(그래프 원본 층 CHECKED.i 차례).
- 절 번호: 그래프는 새 번호(MG-MM 3.0) · Pack 출력은 앱이 쓰는 옛 번호 → 번호-대응.csv로 옛 번호 칸(app_no)만 붙임(그래프 관계에는 0).
- :Knowhow 줄(llmops-2 설계 126 — 시험용 꼬리표에만 있음)은 있으면 그대로 실음.

kg-test/.venv-kg/bin/python -B kg-test/graph-v3/export_release.py [--layer mm3-v2] [--out pack/templates/graph/current.json]"""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import build_v3 as B
from conn import driver

KG = B.KG


def old_numbers(path=B.MAP):
    with Path(path).open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["종류"] in ("점검", "사전 확인", "조치", "공통", "재가동", "마감")]
    return {r["새_절"]: r["옛_절"] for r in rows}


def export(layer, drv):
    with drv.session() as s:
        meta = s.run("MATCH (m:Layer {layer: $v}) RETURN properties(m) AS p", v=layer).single()
        if meta is None:
            raise ValueError(f"그래프에 꼬리표 {layer} 없음")
        groups = s.run("MATCH (r:Record {layer: $v}) RETURN DISTINCT r.group AS name, r.group_code AS code ORDER BY code", v=layer).data()
        sections = s.run("MATCH (x:Section {layer: $v}) WHERE x.kind <> '예외' RETURN x.no AS no, x.name AS name, x.kind AS kind, x.group AS group, x.grade AS grade, x.minutes AS minutes, x.checklist AS checklist, x.desc AS desc ORDER BY x.no", v=layer).data()
        ex_sec = s.run("MATCH (x:Section {layer: $v, kind: '예외'}) RETURN properties(x) AS p ORDER BY x.no", v=layer).data()   # D-545 예외 절(적재기 --nodes)
        ex_rel = s.run("MATCH (e:ExRel {layer: $v}) RETURN properties(e) AS p ORDER BY e.id", v=layer).data()
        causes = s.run("MATCH (c:Cause {layer: $v}) RETURN c.code AS code, c.name AS name ORDER BY c.code", v=layer).data()
        ind = s.run("MATCH (x:Section {layer: $v})-[r:INDICATES]->(c:Cause) RETURN x.no AS no, c.code AS code, r.n AS n ORDER BY no, code", v=layer).data()
        rem = s.run("MATCH (c:Cause {layer: $v})-[r:REMEDIED_BY]->(x:Section) RETURN c.code AS code, x.no AS no, r.n AS n ORDER BY code, no", v=layer).data()
        cand = s.run("MATCH (g:Group {layer: $v})-[r:CANDIDATE]->(c:Cause) RETURN g.name AS group, c.code AS code, r.n AS n ORDER BY group, code", v=layer).data()
        order = s.run("MATCH (r:Record {layer: $v})-[:CONFIRMED]->(c:Cause), (r)-[a:CHECKED {result: '이상'}]->(x:Section)-[:INDICATES]->(c) "
                      "RETURN r.id AS id, c.code AS code, x.no AS no, a.i AS i ORDER BY id, i", v=layer).data()
        recs = s.run("MATCH (r:Record {layer: $v})-[:CONFIRMED]->(c:Cause) RETURN properties(r) AS p, c.code AS cause ORDER BY r.id", v=layer).data()
        work = s.run("MATCH (r:Record {layer: $v})-[a:DID|CHECKED]->(x:Section) RETURN r.id AS id, type(a) AS t, x.no AS no, a.i AS i ORDER BY id, i", v=layer).data()   # D-570 이력이 한 일
        kh = sorted(s.run("MATCH (k:Knowhow {layer: $v}) RETURN properties(k) AS p", v=layer).data(), key=lambda k: str(k["p"].get("knowhow_id")))
    first = Counter()   # (원인, 점검) → 그 원인의 이상 점검 가운데 먼저였던 횟수
    by_rec = defaultdict(list)
    for o in order:
        by_rec[(o["id"], o["code"])].append(o["no"])
    for (_, code), nos in by_rec.items():
        if len(set(nos)) > 1:
            first[(code, nos[0])] += 1
    per = defaultdict(list)
    for x in ind:
        per[x["code"]].append(x)
    old = old_numbers()
    for code, xs in per.items():
        if len(xs) == 1:
            xs[0]["role"] = "confirm"
        else:
            scr = max(xs, key=lambda x: (first[(code, x["no"])], -int(x["no"].split(".")[-1])))
            for x in xs:
                x["role"] = "screen" if x is scr else "confirm"
    for row in sections:
        row["app_no"] = old.get(row["no"])
        if row.get("desc") is None:   # D-560: 점검 설명은 점검 절에만(없는 절은 칸 0 · 옛 꼴 그대로)
            row.pop("desc", None)
    sec_old = {r["no"]: r["app_no"] for r in sections}
    for x in ind + rem:
        x["app_no"] = sec_old[x["no"]]
    did = defaultdict(lambda: {"DID": [], "CHECKED": []})   # D-570: 이력 → 한 조치 · 점검 절(앱 번호 · 적은 차례) — 최근 정비 조건 대조
    for w in work:
        did[w["id"]][w["t"]].append(sec_old[w["no"]])
    sections += [{"steps": [], "criteria": None, "checklist": [], "records": [], **{k: v for k, v in r["p"].items() if k != "layer" and not k.endswith("_json")},
                  **{k[:-5]: json.loads(v) for k, v in r["p"].items() if k.endswith("_json")}} for r in ex_sec]   # D-554 점검 · 조치 칸   # 예외 절: app_no = no(꼴 정본 handoff/d545/graph-shape.md 2절 · 빈 값은 그래프 DB가 칸째 안 남김)
    rels = []
    for r in ex_rel:
        x = {k: v for k, v in r["p"].items() if k not in ("layer", "name") and not k.endswith("_json")}   # name = 그래프 화면 표시 이름(D-619 · release 밖)
        rels += [{"cause": None, "cause_name": None, "records": [], **x, **{k[:-5]: json.loads(v) for k, v in r["p"].items() if k.endswith("_json")}}]
    body = {"layer": layer, "source": {k: meta["p"][k] for k in ("version", "manual_sha256", "history_sha256", "map_sha256", "records", "base_layer", "nodes_sha256", "exception_records") if k in meta["p"]},
            "groups": groups, "sections": sections, "causes": causes, "indicates": ind, "remedied_by": rem, "candidate": cand,
            "records": [{"id": r["p"]["id"], "group": r["p"]["group_code"], "cause": r["cause"], "ts": r["p"]["ts"], "equipment": r["p"]["equipment"],
                         "sensors": {f: r["p"][f] for f in B.SENSORS}, "did": did[r["p"]["id"]]["DID"], "checked": did[r["p"]["id"]]["CHECKED"]} for r in recs],
            "knowhow": [k["p"] for k in kh], "exception_relations": rels}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    body["release_id"] = f"graph-{layer}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"
    return body


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", default=B.VERSION)
    ap.add_argument("--out", default=str(KG.parent / "pack/templates/graph/current.json"))
    a = ap.parse_args()
    with driver() as d:
        rel = export(a.layer, d)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rel, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"release_id": rel["release_id"], "sections": len(rel["sections"]), "indicates": len(rel["indicates"]), "records": len(rel["records"]), "knowhow": len(rel["knowhow"])}, ensure_ascii=False))
