"""세 줄 비교 — 관계를 어디서 얻나만 다르게, 같은 시험 사례 · 같은 채점.
  ① 기준선: 사람이 쓴 관계표(MG-MM 2.0 부록 A를 3.0 번호로) + 비슷한 이력 10건 — 지금 길과 같은 꼴(참고 잣대)
  ② RAG만(규칙형 · LLM 0): 비슷한 이력 10건에서 그때그때 — 그 기록들의 이상 나온 점검 · 한 조치만
  ③ 지식그래프: 이력 전체로 쌓아 둔 관계(그래프 DB 사본 · 꼬리표 kgh-v1 · graph-v3와 같은 꼴)
세 줄 모두 점검 순서는 같은 증상군 이력 가운데 센서가 가장 가까운 10건의 '이상 나온 점검' 표로 정함(같은 표). 다른 것:
불러올 점검 목록 · 표 없는 점검의 차례 · 점검이 이상일 때 붙이는 조치. 목록이 끝나도 정답이 안 나오면 매뉴얼 3.0 차례로 남은 점검을 이어 함(셋 다 같음).

  ③′ 지식그래프 · 근거 건수 순서: ③과 같은 관계 · 순서만 그래프 근거 건수(비슷한 10건 0 — Pack 순서 신호 evidence와 같은 뜻)
시험 사례 = c1a15-eval kg-hidden: history.jsonl(② · ③ 만들기) · alarms.jsonl(입력 · 알람 화면 칸만) · answers.jsonl · case-categories.csv(채점에만 ·
맨 끝에 읽음) · groups.csv 읽지 않음. 갈래 = eval이 낸 예외 갈래 이름(규칙 내용 0).
② · ③은 relations/ · rules.yaml · 2.0 매뉴얼 · answers를 열지 않음(test_compare.py가 연 파일을 셈). 조치는 이력에서만(점검 번호로 짐작 0).
출력은 개수 · 비율만(results/세줄.json · 세줄.md) — 사례별 정답 값 0.

kg-test/.venv-kg/bin/python -B kg-test/compare3/compare.py --hidden <kg-hidden 폴더> --manual20 <앱 저장소>/data/manuals/MG-MM.md"""
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
KG = HERE.parent
sys.path.insert(0, str(KG / "graph-v3"))
import build_v3 as B  # noqa: E402

LAYER = "kgh-v1"
K = 10
ALLOWED = {"history.jsonl", B.MANUAL.name}   # ② · ③이 받는 파일 이름
ALARM_KEYS = {"case_id", "machine", "line", "alarm_at", "shift", "shift_started_at", "group", "group_name", "direction",
              "product_grade", "sensor_snapshot", "line_alarms_30min", "recent_work_60d"}
LINES = ("① 관계표+RAG", "② RAG만", "③ 지식그래프", "③′ 근거 건수 순서")
REFS = ("참고: 매뉴얼 차례만",)   # 순서 신호를 가르는 참고 줄


class Leak(ValueError):
    pass


def _jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def _allowed(*paths):
    for p in paths:
        if Path(p).name not in ALLOWED:
            raise Leak(f"② · ③ 입력은 이력과 매뉴얼 3.0만: {Path(p).name}")


def check_alarms(alarms):
    for a in alarms:
        if set(a) - ALARM_KEYS:
            raise Leak(f"알람에 화면 밖 칸: {sorted(set(a) - ALARM_KEYS)}")
    return alarms


def sensors(snap):
    s = {f: float(snap[f]) for f in B.SENSORS[:5]}
    s["temp_diff_k"] = round(s["process_temperature_k"] - s["air_temperature_k"], 1)
    s["power_w"] = round(s["torque_nm"] * s["rotational_speed_rpm"] * 2 * math.pi / 60)
    return s


def cause_name(text):
    return text.split(" — ")[0].strip()   # 정비자가 덧붙인 상황 말은 뗌


def checks_of(manual):
    """매뉴얼 3.0 증상군 이름 → 점검 절(매뉴얼 차례) · 절 → 분."""
    secs = B.manual_sections(manual)
    by = defaultdict(list)
    for s in secs:
        if s["kind"] == "점검":
            by[s["group"]].append(s["no"])
    return dict(by), {s["no"]: s["minutes"] or 0 for s in secs}


def history_rows(history, manual=B.MANUAL):
    """② 입력 — 이력 한 건 = 증상군 · 센서 · 이상 나온 점검(한 차례) · 한 조치."""
    _allowed(history, manual)
    out = []
    for r in _jsonl(history):
        out.append({"id": r["record_id"], "group": r["group_name"], "sensors": sensors(r["sensor_snapshot"]),
                    "abnormal": [c["section"] for c in r["checks"] if c["result"] == "이상"],
                    "action": (r.get("action") or {}).get("section"), "cause": cause_name(r["report"]["cause_text"]) if r["outcome"] == "원인 확정" else None})
    return out


def graph_rows(history, manual=B.MANUAL):
    """③ 입력 — kg-hidden 이력 꼴 → build_v3.graph_rows와 같은 꼴(원인 마디 = 보고서 원인 글)."""
    _allowed(history, manual)
    secs = B.manual_sections(manual)
    known = {s["no"] for s in secs}
    groups = sorted({s["group"] for s in secs if s["group"]})
    records, checked, did, confirmed, causes = [], [], [], [], set()
    ind, rem, cand = Counter(), Counter(), Counter()
    for r in sorted(_jsonl(history), key=lambda x: x["record_id"]):
        g = r["group_name"]
        if g not in groups:
            raise ValueError(f"{r['record_id']}: 매뉴얼에 없는 증상군")
        rid = r["record_id"]
        records.append({"id": rid, "name": rid, "group": g, "group_code": r["group"], "ts": r["alarm_at"], "equipment": r["machine"],
                        "outcome": r["outcome"], **sensors(r["sensor_snapshot"])})
        mine = []
        for i, c in enumerate(r["checks"]):
            if c["section"] not in known:
                raise ValueError(f"{rid}: 매뉴얼에 없는 절")
            mine.append({"id": rid, "no": c["section"], "result": c["result"], "i": i})
        checked += mine
        act = (r.get("action") or {}).get("section")
        if act:
            if act not in known:
                raise ValueError(f"{rid}: 매뉴얼에 없는 절")
            did.append({"id": rid, "no": act, "i": len(r["checks"])})
        if r["outcome"] == "원인 확정":
            code = cause_name(r["report"]["cause_text"])
            causes.add(code)
            confirmed.append({"id": rid, "code": code})
            cand[(g, code)] += 1
            for c in mine:
                if c["result"] == "이상":
                    ind[(c["no"], code)] += 1
            if act:
                rem[(code, act)] += 1
    meta = {"version": LAYER, "manual_sha256": B._sha(manual), "history_sha256": B._sha(history), "map_sha256": "", "records": len(records)}
    return {"meta": meta, "groups": [{"name": g} for g in groups], "sections": secs,
            "has_section": [{"group": s["group"], "no": s["no"]} for s in secs if s["group"]],
            "causes": [{"code": c, "name": c} for c in sorted(causes)],
            "records": records, "checked": checked, "did": did, "confirmed": confirmed,
            "indicates": [{"no": a, "code": b, "n": n} for (a, b), n in sorted(ind.items())],
            "remedied_by": [{"code": a, "no": b, "n": n} for (a, b), n in sorted(rem.items())],
            "candidate": [{"group": a, "code": b, "n": n} for (a, b), n in sorted(cand.items())]}


def read_graph(drv, layer=LAYER):
    """③이 쓰는 값은 모두 그래프 DB에서 다시 읽음(파이썬 행 0)."""
    with drv.session() as s:
        recs = s.run("MATCH (r:Record {layer: $v}) RETURN properties(r) AS p ORDER BY r.id", v=layer).data()
        ab = s.run("MATCH (r:Record {layer: $v})-[c:CHECKED {result: '이상'}]->(x:Section) RETURN r.id AS id, x.no AS no ORDER BY id, c.i", v=layer).data()
        ind = s.run("MATCH (x:Section {layer: $v})-[r:INDICATES]->(c:Cause) RETURN x.no AS no, c.code AS c, r.n AS n", v=layer).data()
        rem = s.run("MATCH (c:Cause {layer: $v})-[r:REMEDIED_BY]->(x:Section) RETURN c.code AS c, x.no AS no, r.n AS n", v=layer).data()
        cand = s.run("MATCH (g:Group {layer: $v})-[r:CANDIDATE]->(c:Cause) RETURN g.name AS g, c.code AS c, r.n AS n", v=layer).data()
    abn = defaultdict(list)
    for x in ab:
        abn[x["id"]].append(x["no"])
    rows = [{"id": r["p"]["id"], "group": r["p"]["group"], "sensors": {f: r["p"][f] for f in B.SENSORS}, "abnormal": abn[r["p"]["id"]]} for r in recs]
    return {"records": rows, "ind": ind, "rem": rem, "cand": cand}


def human_table(manual20):
    """① 입력 — 사람이 쓴 관계표(2.0 부록 A) → 3.0 번호. 증상군 이름 → [(선별, 확정, 조치)] 표 차례."""
    import check_v3 as C   # 2.0 부록 A 읽기는 이 함수만
    tab = defaultdict(list)
    for r in C.gold_rows(manual20, B.number_map()):
        tab[r["group"]].append((r["screen"], r["confirm"], r["action"]))
    return dict(tab)


def _scale(rows):
    sc = {}
    for f in B.SENSORS:
        v = [r["sensors"][f] for r in rows]
        m = sum(v) / len(v)
        sc[f] = math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1.0
    return sc


def similar(alarm, rows, sc, k=K):
    a = sensors(alarm["sensor_snapshot"])
    pool = [r for r in rows if r["group"] == alarm["group_name"]]
    d = lambda r: math.sqrt(sum(((a[f] - r["sensors"][f]) / sc[f]) ** 2 for f in B.SENSORS) / len(B.SENSORS))
    return sorted(pool, key=lambda r: (d(r), r["id"]))[:k]


def _votes(sim):
    v, first = Counter(), {}
    for i, r in enumerate(sim):
        for no in r["abnormal"]:
            v[no] += 1
            first.setdefault(no, i)
    return v, first


def plan_human(alarm, sim, tab, order):
    rows = tab.get(alarm["group_name"], [])
    act = {}
    for screen, confirm, action in rows:   # 확정 점검의 조치 먼저 · 선별만인 점검은 표 첫 줄 조치
        act.setdefault(confirm, action)
    for screen, confirm, action in rows:
        if screen:
            act.setdefault(screen, action)
    v, _ = _votes(sim)
    man = order[alarm["group_name"]]
    loaded = sorted(act, key=lambda no: (-v[no], man.index(no) if no in man else 99, no))
    return {"order": loaded, "action": act}


def plan_rag(alarm, sim):
    v, first = _votes(sim)
    loaded = sorted(v, key=lambda no: (-v[no], first[no]))
    act = {}
    for no in loaded:
        c = Counter()
        rank = {}
        for i, r in enumerate(sim):
            if no in r["abnormal"] and r.get("action"):
                c[r["action"]] += 1
                rank.setdefault(r["action"], i)
        if c:
            act[no] = max(c, key=lambda a: (c[a], -rank[a]))
    return {"order": loaded, "action": act}


def plan_graph(alarm, sim, kg, votes=True):
    g = alarm["group_name"]
    gc = {x["c"] for x in kg["cand"] if x["g"] == g}
    w = Counter()
    by_check = defaultdict(Counter)
    for x in kg["ind"]:
        if x["c"] in gc:
            w[x["no"]] += x["n"]
            by_check[x["no"]][x["c"]] = x["n"]
    rem = defaultdict(Counter)
    for x in kg["rem"]:
        rem[x["c"]][x["no"]] = x["n"]
    v, _ = _votes(sim) if votes else (Counter(), {})
    loaded = sorted(w, key=lambda no: (-v[no], -w[no], no))
    act = {}
    for no in loaded:
        c = max(sorted(by_check[no]), key=lambda c: by_check[no][c])
        if rem[c]:
            act[no] = max(sorted(rem[c]), key=lambda a: rem[c][a])
    return {"order": loaded, "action": act}


def score(plan, truth, man, minutes):
    """첫 점검 · 헛걸음(건 · 분) · 불러온 점검 · 조치 — 목록 뒤는 매뉴얼 차례로 남은 점검."""
    full = plan["order"] + [no for no in man if no not in plan["order"]]
    want = truth["confirm_check"]
    i = full.index(want) if want in full else len(full)
    return {"first": full[0] == want, "waste_n": i, "waste_min": sum(minutes.get(no, 0) for no in full[:i]),
            "loaded": want in plan["order"], "action": plan["action"].get(want) == truth["action"]}


def run(hidden, manual20, drv, manual=B.MANUAL, layer=LAYER):
    hidden = Path(hidden)
    alarms = check_alarms(_jsonl(hidden / "alarms.jsonl"))
    order, minutes = checks_of(manual)
    hist = history_rows(hidden / "history.jsonl", manual)
    B.put(graph_rows(hidden / "history.jsonl", manual), layer, drv)
    kg = read_graph(drv, layer)
    tab = human_table(manual20)
    sc_h, sc_g = _scale(hist), _scale(kg["records"])
    plans = []
    for a in alarms:
        sim_h, sim_g = similar(a, hist, sc_h), similar(a, kg["records"], sc_g)
        plans.append((a, {LINES[0]: plan_human(a, sim_h, tab, order), LINES[1]: plan_rag(a, sim_h), LINES[2]: plan_graph(a, sim_g, kg),
                          LINES[3]: plan_graph(a, sim_g, kg, votes=False), REFS[0]: {"order": list(order[a["group_name"]]), "action": {}}}))
    truth = {x["case_id"]: x["truth"] for x in _jsonl(hidden / "answers.jsonl")}   # 채점에만 — 여기서 처음 읽음
    cat = categories(hidden / "case-categories.csv")   # 채점 묶음에만 — 여기서 처음 읽음
    return tally(plans, truth, order, minutes, cat), relation_overlap(tab, plans)


def categories(path):
    import csv
    with Path(path).open(encoding="utf-8") as f:
        return {r["case_id"]: r["category"] for r in csv.DictReader(f)}


def tally(plans, truth, order, minutes, cat):
    rows = defaultdict(lambda: defaultdict(list))
    other = Counter()
    for a, ps in plans:
        t = truth[a["case_id"]]
        if t["outcome"] != "원인 확정":
            other[t["outcome"]] += 1
            continue
        keys = ["모두", f"증상군 {a['group']}", f"갈래 {cat[a['case_id']]}"]
        for line, p in ps.items():
            s = score(p, t, order[a["group_name"]], minutes)
            for k in keys:
                rows[k][line].append(s)
    out = []
    for k, per in rows.items():
        for line in LINES + REFS:
            ss = per[line]
            n = len(ss)
            out.append({"row": k, "line": line, "n": n, "first": round(sum(s["first"] for s in ss) / n, 3),
                        "waste_n": round(sum(s["waste_n"] for s in ss) / n, 2), "waste_min": round(sum(s["waste_min"] for s in ss) / n, 1),
                        "loaded": round(sum(s["loaded"] for s in ss) / n, 3), "action": round(sum(s["action"] for s in ss) / n, 3)})
    return {"rows": out, "not_confirmed": dict(other)}


def relation_overlap(tab, plans):
    """① 사람 관계표와 얼마나 가까운가 — 점검 → 조치 짝(사례마다 붙인 조치)이 관계표와 같은 몫."""
    out = {}
    for line in LINES[1:]:   # ③′는 ③과 조치가 같음
        same = diff = none = 0
        for _, ps in plans:
            for no in ps[LINES[0]]["order"]:
                got = ps[line]["action"].get(no)
                if got is None:
                    none += 1
                elif got == ps[LINES[0]]["action"][no]:
                    same += 1
                else:
                    diff += 1
        out[line] = {"같음": same, "다름": diff, "없음": none}
    return out


ORDER_ROWS = ["모두", "증상군 TW", "증상군 PW", "증상군 CL", "증상군 LD", "갈래 예외 없음", "갈래 교체 직후", "갈래 같은 라인 동시",
              "갈래 몰래 넣은 예외 1", "갈래 몰래 넣은 예외 2", "갈래 몰래 넣은 예외 3"]


def write(res, overlap, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "세줄.json").write_text(json.dumps({"layer": LAYER, **res, "overlap": overlap}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = ["세 줄 비교 — kg-hidden 원인 확정 사례(개수 · 비율만 · 사례별 정답 값 0)", "",
          "| 묶음 | 줄 | 사례 | 첫 점검 맞음 | 헛걸음 평균(건 · 분) | 불러온 점검 | 조치 맞음 |", "|---|---|---|---|---|---|---|"]
    by = {(r["row"], r["line"]): r for r in res["rows"]}
    for k in ORDER_ROWS + sorted({r["row"] for r in res["rows"]} - set(ORDER_ROWS)):
        for line in LINES:
            r = by.get((k, line))
            if r:
                md.append(f"| {k} | {line} | {r['n']} | {r['first']:.0%} | {r['waste_n']:.2f} · {r['waste_min']:.1f}분 | {r['loaded']:.0%} | {r['action']:.0%} |")
    md += ["", "참고 — 매뉴얼 차례만(조치 · 불러온 점검은 셈 밖)", "", "| 묶음 | 줄 | 첫 점검 맞음 | 헛걸음 평균(건 · 분) |", "|---|---|---|---|"]
    for k in ORDER_ROWS:
        for line in REFS:
            r = by[(k, line)]
            md.append(f"| {k} | {line} | {r['first']:.0%} | {r['waste_n']:.2f} · {r['waste_min']:.1f}분 |")
    md += ["", f"원인 확정이 아닌 사례(채점 밖): {', '.join(f'{k} {v}' for k, v in sorted(res['not_confirmed'].items()))}",
           "", "① 관계표와 같은 점검 → 조치 짝(사례 × ① 목록 점검): " + " · ".join(f"{k} 같음 {v['같음']} · 다름 {v['다름']} · 없음 {v['없음']}" for k, v in overlap.items())]
    (out / "세줄.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return "\n".join(md)


if __name__ == "__main__":
    from conn import driver
    ap = argparse.ArgumentParser()
    ap.add_argument("--hidden", required=True)
    ap.add_argument("--manual20", required=True)
    ap.add_argument("--out", default=str(HERE / "results"))
    a = ap.parse_args()
    with driver() as d:
        res, ov = run(a.hidden, a.manual20, d)
    print(write(res, ov, Path(a.out)))
