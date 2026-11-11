"""D-536 ③ 알람별 확인 — 그래프(mm3-v1 · 이력에서 셈)를 숨긴 정답(MG-MM 2.0 부록 A 원인–점검–조치)과 대조.
정답은 이 스크립트만 읽음(그래프 만드는 build_v3.py는 읽지 않음 — test_v3.py). 정답의 옛 절 번호는 번호-대응.csv로 새 번호로 바꿔 견줌.
화면 · 대조.json 출력은 개수 · 칸 이름만. 원인–점검 짝 글은 사용자용 문서(알람별-확인.html)에만.

kg-test/.venv-kg/bin/python -B kg-test/graph-v3/check_v3.py --gold <앱 저장소>/data/manuals/MG-MM.md [--out kg-test/graph-v3/results]"""
import argparse
import html
import json
import re
from collections import defaultdict
from pathlib import Path

import build_v3 as B
from conn import driver

GROUP_ORDER = ["공구 마모 경고", "출력 이탈", "온도 차 저하", "토크 과부하"]


def gold_rows(path, nmap):
    t = Path(path).read_text(encoding="utf-8")
    part = t.split("## 부록 A.")[1].split("\n## ")[0]
    out = []
    for l in part.splitlines():
        c = [x.strip() for x in l.strip().strip("|").split("|")]
        if len(c) != 5 or not re.match(r"[A-Z]{2}-\d", c[0]):
            continue
        code, name = c[0].split(" ", 1)
        act = c[4].split(" ", 1)[0]
        out.append({"code": code, "name": name, "group": c[1],
                    "screen": None if c[2] == "—" else nmap[c[2]], "confirm": nmap[c[3]], "action": nmap[act]})
    return out


def graph_facts(drv, v):
    with drv.session() as s:
        sec = {r["no"]: r for r in s.run("MATCH (x:Section {layer: $v}) RETURN x.no AS no, x.name AS name, x.kind AS kind, x.group AS group, x.grade AS grade, x.minutes AS minutes", v=v).data()}
        cand = s.run("MATCH (g:Group {layer: $v})-[r:CANDIDATE]->(c:Cause) RETURN g.name AS g, c.code AS c, c.name AS cn, r.n AS n", v=v).data()
        ind = s.run("MATCH (x:Section {layer: $v})-[r:INDICATES]->(c:Cause) RETURN x.no AS no, c.code AS c, r.n AS n", v=v).data()
        rem = s.run("MATCH (c:Cause {layer: $v})-[r:REMEDIED_BY]->(x:Section) RETURN c.code AS c, x.no AS no, r.n AS n", v=v).data()
        reps = {}
        for g in GROUP_ORDER:   # 대표 알람: 증상군마다 원인 확정 첫 건 · 출력 이탈은 이상 둘 · 토크 과부하는 범위 밖 점검이 든 건
            extra = {"출력 이탈": "AND COUNT { (r)-[c:CHECKED]->() WHERE c.result = '이상' } >= 2",
                     "토크 과부하": "AND EXISTS { (r)-[c:CHECKED]->(:Section {no: '2.4.6'}) WHERE c.result = '이상' }"}.get(g, "")
            row = s.run(f"MATCH (r:Record {{layer: $v, group: $g}})-[:CONFIRMED]->() WHERE true {extra} RETURN r.id AS id ORDER BY r.id LIMIT 1", v=v, g=g).single()
            reps[g] = row["id"] if row else None
        rec_n = s.run("MATCH (r:Record {layer: $v}) RETURN count(r) AS n", v=v).single()["n"]
    return sec, cand, ind, rem, reps, rec_n


def compare(gold, cand, ind, rem):
    g_of = defaultdict(dict)
    for x in cand:
        g_of[x["c"]][x["g"]] = x["n"]
    chk = defaultdict(dict)
    for x in ind:
        chk[x["c"]][x["no"]] = x["n"]
    act = defaultdict(dict)
    for x in rem:
        act[x["c"]][x["no"]] = x["n"]
    rows, links = [], {"맞음": 0, "빠짐": 0, "틀림": 0}
    for r in gold:
        res = {}
        for part, want, got in (("증상군", {r["group"]}, set(g_of[r["code"]])),
                                ("점검", {x for x in (r["screen"], r["confirm"]) if x}, set(chk[r["code"]])),
                                ("조치", {r["action"]}, set(act[r["code"]]))):
            hit, miss, extra = want & got, want - got, got - want
            links["맞음"] += len(hit)
            links["빠짐"] += len(miss)
            links["틀림"] += len(extra)
            res[part] = "틀림" if extra else "빠짐" if miss else "맞음"
        res["원인"] = "틀림" if "틀림" in res.values() else "빠짐" if "빠짐" in res.values() else "맞음"
        rows.append({**r, "res": res, "checks_n": chk[r["code"]], "actions_n": act[r["code"]], "groups_n": g_of[r["code"]]})
    extra_causes = sorted(set(g_of) - {r["code"] for r in gold})
    return rows, links, extra_causes


def first_check(group, sec, cand, ind):
    """증상군의 첫 점검(그래프 순서) — A·B 점검 가운데 (가리키는 원인 비율 합) ÷ 소요 분이 가장 큰 것. 비율은 이력 원인 건수."""
    tot = sum(x["n"] for x in cand if x["g"] == group)
    share = {x["c"]: x["n"] / tot for x in cand if x["g"] == group}
    score = defaultdict(float)
    for x in ind:
        s = sec[x["no"]]
        if s["group"] == group and s["grade"] in ("A", "B") and x["c"] in share:
            score[x["no"]] += share[x["c"]]
    ranked = sorted(score, key=lambda no: (-score[no] / sec[no]["minutes"], no))
    return ranked, {no: round(score[no], 3) for no in ranked}


def main(gold_path, out):
    nmap = B.number_map()
    gold = gold_rows(gold_path, nmap)
    with driver() as d:
        sec, cand, ind, rem, reps, rec_n = graph_facts(d, B.VERSION)
    rows, links, extra = compare(gold, cand, ind, rem)
    per = {k: {v: sum(1 for r in rows if r["res"][k] == v) for v in ("맞음", "빠짐", "틀림")} for k in ("원인", "증상군", "점검", "조치")}
    groups = []
    for g in GROUP_ORDER:
        gc = [r for r in rows if r["group"] == g]
        want_chk = {x for r in gc for x in (r["screen"], r["confirm"]) if x}
        got_chk = {x["no"] for x in ind if sec[x["no"]]["group"] == g}
        manual_chk = {no for no, s in sec.items() if s["group"] == g and s["kind"] == "점검"}
        got_act = {x["no"] for x in rem if x["c"] in {r["code"] for r in gc}}
        ranked, score = first_check(g, sec, cand, ind)
        groups.append({"group": g, "causes_gold": len(gc), "causes_graph": sum(1 for x in cand if x["g"] == g),
                       "checks_manual": len(manual_chk), "checks_graph": len(got_chk), "checks_gold_hit": len(want_chk & got_chk),
                       "actions_gold": len({r["action"] for r in gc}), "actions_graph": len(got_act),
                       "first_check": ranked[0] if ranked else None, "order": ranked, "score": score,
                       "records": sum(x["n"] for x in cand if x["g"] == g), "rep": reps[g]})
    summary = {"layer": B.VERSION, "records": rec_n, "gold_causes": len(gold), "graph_causes": len({x["c"] for x in cand}),
               "per_cause": per, "links": links, "graph_causes_not_in_gold": len(extra),
               "role_screen_vs_confirm": "구별 안 됨(이력에는 '이상'만 남음)",
               "groups": [{k: v for k, v in x.items() if k not in ("first_check", "order", "score", "rep")} for x in groups],
               "reps": len([x for x in groups if x["rep"]])}
    out.mkdir(parents=True, exist_ok=True)
    (out / "대조.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "대표-알람.json").write_text(json.dumps({x["group"]: x["rep"] for x in groups}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    doc(out, rows, groups, sec, links, per, rec_n)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def noise_html(out):
    """보강 1 — 잡음 섞인 이력 대조(noise.py가 쓴 잡음.json이 있으면)."""
    f = out / "잡음.json"
    if not f.exists():
        return ""
    d = json.loads(f.read_text(encoding="utf-8"))
    tr = "".join(f"<tr><td>{r['rate']:.0%}</td><td>{r['noise']['빈 점검 결과']} · {r['noise']['잘못 적힌 원인']} · {r['noise']['빠진 조치']}</td>"
                 f"<td>{r['min_n'] if isinstance(r['min_n'], str) else str(r['min_n']) + '건 이상'}</td><td>{r['cause']['맞음']} · {r['cause']['빠짐']} · {r['cause']['틀림']}</td>"
                 f"<td>{r['links']['맞음']} · {r['links']['빠짐']} · {r['links']['틀림']}</td></tr>" for r in d["rows"])
    return (f"<h2>4. 기록이 틀리게 섞이면 — 잡음 섞인 이력으로 같은 대조</h2><p>이력 사본에 일부러 잡음을 넣었습니다(원본은 그대로 · 씨앗 {d['seed']}). "
            "한 건마다 정한 비율로 ① 이상이던 점검 결과 칸을 비우고 ② 원인을 같은 증상군의 다른 원인으로 바꿔 적고 ③ 조치 단계를 지웠습니다. "
            "'받는 근거'는 그래프 연결을 몇 건부터 믿을지입니다(몫 30% = 같은 원인의 연결 가운데 가장 큰 근거의 30% 이상만).</p>"
            f"<table><tr><th>잡음 비율</th><th>넣은 잡음<br>(빈 결과 · 틀린 원인 · 빠진 조치)</th><th>받는 근거</th><th>원인 14<br>맞음 · 빠짐 · 틀림</th><th>연결 44<br>맞음 · 빠짐 · 틀림</th></tr>{tr}</table>"
            "<div class='box'><b>읽는 법</b> — 근거 1건부터 받으면 잡음 5%에도 엉뚱한 연결이 붙습니다. 근거를 거르면 5%까지는 그대로고, 10%부터 틀린 연결이 생깁니다. "
            "그래프를 쓸 때는 '근거가 충분한 연결만' 받는 거름이 꼭 있어야 합니다.</div>")


def doc(out, rows, groups, sec, links, per, rec_n):
    """사용자용 문서(쉬운 말) — 원인–점검–조치 짝 글은 여기만."""
    e = html.escape
    nm = lambda no: f"{no} {sec[no]['name']}" if no else "—"
    mark = {"맞음": "맞음", "빠짐": "빠짐", "틀림": "틀림"}
    tr = []
    for r in rows:
        got_chk = " · ".join(f"{nm(no)} ({n}건)" for no, n in sorted(r["checks_n"].items())) or "없음"
        got_act = " · ".join(f"{nm(no)} ({n}건)" for no, n in sorted(r["actions_n"].items())) or "없음"
        want_chk = " · ".join(nm(x) for x in (r["screen"], r["confirm"]) if x)
        tr.append(f"<tr><td>{e(r['code'])} {e(r['name'])}</td><td>{e(r['group'])}</td><td>{e(want_chk)}</td><td>{e(got_chk)}</td>"
                  f"<td>{e(nm(r['action']))}</td><td>{e(got_act)}</td><td class='m {r['res']['원인']}'>{mark[r['res']['원인']]}</td></tr>")
    gr = []
    for g in groups:
        order = " → ".join(nm(no) for no in g["order"])
        gr.append(f"<tr><td>{e(g['group'])}</td><td>{g['records']}</td><td>{g['causes_graph']} / {g['causes_gold']}</td>"
                  f"<td>{g['checks_graph']} / {g['checks_manual']}</td><td>{g['actions_graph']} / {g['actions_gold']}</td><td>{e(nm(g['first_check']))}</td><td>{e(order)}</td></tr>")
    pics = "".join(f"<figure><img src='alarm-{e(g['rep'])}.png'><figcaption>{e(g['group'])} 알람 {e(g['rep'])} — 둘레 그래프(그래프 DB 화면 그대로)</figcaption></figure>"
                   for g in groups if g["rep"] and (out / f"alarm-{g['rep']}.png").exists())
    css = """@page{size:A4;margin:16mm}body{font-family:'Apple SD Gothic Neo',sans-serif;color:#1b1f24;font-size:10pt;line-height:1.55;word-break:keep-all}
h1{font-size:19pt;margin:0 0 4pt}h2{font-size:13pt;margin:18pt 0 6pt;border-bottom:1.2pt solid #1b1f24;padding-bottom:3pt}.sub{color:#5b6470;margin:0 0 12pt}
.box{border-left:4pt solid #1f5aa6;background:#f3f6fa;padding:7pt 10pt;margin:8pt 0}.warn{border-left-color:#c8323c;background:#fbf1f1}
table{border-collapse:collapse;width:100%;font-size:8.6pt;margin:6pt 0}th,td{border:.7pt solid #9aa3ad;padding:3pt 5pt;text-align:left;vertical-align:top}th{background:#e9ecef}
td.m{font-weight:700;white-space:nowrap}td.맞음{color:#1d6b3a}td.빠짐{color:#9a6a00}td.틀림{color:#c8323c}.big{display:flex;gap:10pt;margin:8pt 0}
.big div{flex:1;border:1pt solid #c9cfd6;padding:8pt;text-align:center}.big b{display:block;font-size:20pt}figure{margin:10pt 0;break-inside:avoid}
figure img{display:block;max-width:100%;max-height:118mm;margin:0 auto;border:.8pt solid #c9cfd6}figcaption{font-size:8.6pt;color:#3a424c}"""
    body = f"""<h1>지식그래프 알람별 확인</h1><p class="sub">관계표 없는 매뉴얼(MG-MM 3.0) + 승인된 정비 이력 {rec_n}건 → 그래프 · 2026-11-03</p>
<div class="box"><b>무엇을 봤나</b> — 매뉴얼에서 '어떤 점검이 어떤 원인을 확인하는지 · 원인마다 어떤 조치를 하는지' 표를 모두 뺐습니다. 그 뒤 과거 정비 이력만으로 그래프를 만들고, 뺀 표(옛 매뉴얼 부록 A)와 견줘 연결이 다시 살아났는지 봤습니다. 그래프를 만드는 쪽은 옛 표를 읽지 않습니다.</div>
<div class="big"><div><b>{per['원인']['맞음']} / {len(rows)}</b>원인 단위 맞음</div><div><b>{links['맞음']}</b>맞은 연결</div><div><b>{links['빠짐']}</b>빠진 연결</div><div><b>{links['틀림']}</b>틀린(넘친) 연결</div></div>
<h2>1. 관계 복원 — 원인마다</h2><p>'정답'은 옛 매뉴얼 부록 A, '그래프'는 이력에서 센 연결입니다(괄호 = 근거 건수). 원인이 '맞음'이면 증상군 · 점검 · 조치가 모두 정답과 같습니다.</p>
<table><tr><th>원인</th><th>증상군</th><th>정답 점검</th><th>그래프 점검</th><th>정답 조치</th><th>그래프 조치</th><th>결과</th></tr>{''.join(tr)}</table>
<div class="box warn"><b>구별 못 하는 것</b> — 정답에서 '먼저 걸러 보는 점검(선별)'과 '원인을 정하는 점검(확정)'으로 나뉜 두 원인은, 그래프에선 두 점검 모두 '이상이 나왔던 점검'으로만 잇습니다. 이력에 그 구별이 남지 않기 때문입니다.</div>
<h2>2. 증상군별 — 불러온 점검 · 조치 · 첫 점검</h2>
<table><tr><th>증상군</th><th>이력</th><th>원인 후보<br>(그래프 / 정답)</th><th>점검<br>(그래프 / 매뉴얼)</th><th>조치<br>(그래프 / 정답)</th><th>첫 점검(그래프)</th><th>점검 순서(그래프)</th></tr>{''.join(gr)}</table>
<p>순서 = 정비자가 혼자 할 수 있는 점검(A · B) 가운데, 그 점검이 가리키는 원인이 이력에서 차지한 비율 ÷ 소요 분이 큰 차례입니다.</p>
<div class="box warn"><b>원인 순서는 뜻이 약합니다</b> — 이 가상 이력의 세부 원인은 센서 값과 상관없이 정해 둔 비율로 뽑았습니다. 그래서 '어떤 원인이 더 자주 나오는가'에서 나온 순서는 우리가 정한 비율을 다시 읽은 것일 뿐입니다. 이 문서에서 믿을 만한 것은 1절의 '연결이 살아났는가'입니다.</div>
<h2>3. 대표 알람</h2><p>그래프 DB 화면에서 알람 한 건 둘레만 그린 그림입니다. 초록 = 원인, 파랑 = 매뉴얼 절(점검 · 조치), 연두 = 증상군, 하늘 = 이 알람 기록.</p>{pics or '<p>(그림 없음)</p>'}{noise_html(out)}
<h2>5. 한계</h2><p>데이터는 가상이고 규칙은 우리가 정했습니다. 이력의 점검 · 원인 · 조치 기록이 깨끗하게 만들어져 있어 연결이 거의 그대로 살아나는 것이 당연한 조건입니다. 실제 현장 기록처럼 빠지거나 틀린 기록이 섞이면 결과가 달라집니다.</p>"""
    (out / "알람별-확인.html").write_text(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>지식그래프 알람별 확인</title><style>{css}</style></head><body>{body}</body></html>\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    a = ap.parse_args()
    main(a.gold, Path(a.out))
