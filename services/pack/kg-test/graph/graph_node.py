"""D-534 관계 질의 노드 — 그래프 DB판. 옵션 셋 · 규칙 표 · 판단(kg_node.decide)은 파일판과 같고, 근거 찾기만 Cypher 질의.
규칙 표 값은 질의 틀에 넣어 씀(칸 이름은 허용 목록으로 검사 · 값은 파라미터) — 그래프에는 규칙을 저장하지 않음.
새 알람의 관계는 그래프에 저장하지 않고 질의할 때 셈(같은 정의 = 그래프 :Layer의 defs)."""
import json

from conn import driver

import kg_node as N   # noqa: E402
import methods as M   # noqa: E402

VISIBLE_S = N.VISIBLE.total_seconds()
SELF_FIELDS = set(M.FIELDS) | {"season", "machine", "line"}
EDGE_FIELDS = {"same_line": {"age_min"}, "machine_work": {"kind", "age_days"}}

# 질의 틀 — {where}에 규칙 표 조건이 들어감
ALARM_SAME_LINE = """MATCH (o:Event {layer: $v}) WHERE o.line = $line AND o.machine <> $machine AND $t - $win * 60 <= o.epoch < $t
WITH {age_min: ($t - o.epoch) / 60} AS r WHERE true {where}
RETURN count(*) >= $n AS hit"""
ALARM_MACHINE_WORK = """MATCH (o:Event {layer: $v}) WHERE o.machine = $machine AND o.work IS NOT NULL AND $t - $days * 86400 <= o.epoch < $t
WITH {kind: o.work, age_days: round(($t - o.epoch) / 86400.0, 4)} AS r WHERE r.age_days * 86400 >= $vis + 1e-6 {where}
RETURN count(*) >= $n AS hit"""
ALARM_SELF = """WITH $alarm AS e WHERE true {where} RETURN count(*) > 0 AS hit"""
EVIDENCE = """MATCH (e:Event {layer: $v})-[:CONFIRMED_CAUSE]->(c:Cause)
WHERE e.epoch < $t - $vis AND ($scope = 'plant' OR ($scope = 'line' AND e.line = $line) OR ($scope = 'machine' AND e.machine = $machine)) {cond}
WITH c.code AS cause, CASE WHEN $h IS NULL THEN 1.0 ELSE 0.5 ^ (($t - e.epoch) / 86400.0 / $h) END AS w
RETURN cause, count(*) AS n, sum(w) AS w"""
EV_SELF = "{where}"
EV_SAME_LINE = "AND COUNT { (e)-[r:SAME_LINE_BEFORE]->() WHERE true {where} } >= $n"
EV_MACHINE_WORK = "AND COUNT { (e)-[r:PRIOR_WORK]->() WHERE r.age_days * 86400 >= $vis + 1e-6 {where} } >= $n"
TEMPLATES = (ALARM_SAME_LINE, ALARM_MACHINE_WORK, ALARM_SELF, EVIDENCE, EV_SELF, EV_SAME_LINE, EV_MACHINE_WORK)


def _where(rule, var, params):
    """규칙 조건 → Cypher 조각(칸 이름은 허용 목록 · 값은 파라미터)."""
    allowed = SELF_FIELDS if rule["rel"] == "self" else EDGE_FIELDS[rule["rel"]]
    out = []
    for i, (f, op, v) in enumerate(rule["where"]):
        if f not in allowed:
            raise N.RuleError(f"{rule['id']}: 그래프에 없는 칸 {f}")
        if v == "@":
            rhs = f"$alarm.{f}"
        else:
            key = f"w{i}"
            try:
                params[key] = float(v)
            except ValueError:
                params[key] = v
            rhs = f"${key}"
        out.append(f"AND {var}.{f} {op} {rhs}")
    return " ".join(out)


class GraphNode:
    def __init__(self, rules, options, k, layer="rel-v1", drv=None, case_ids=()):
        N.Node(None, rules, options, k)   # 옵션 검사는 파일판과 같음
        self.rules, self.opt, self.k, self.v = rules, options, k, layer
        self.drv = drv or driver()
        with self.drv.session() as s:
            meta = s.run("MATCH (m:Layer {layer: $v}) RETURN m.defs AS d", v=layer).single()
            if meta is None:
                raise ValueError(f"그래프에 관계 층 {layer} 없음")
            self.defs = json.loads(meta["d"])
            leaked = s.run("MATCH (e:Event {layer: $v})-[:CONFIRMED_CAUSE]->() WHERE e.id IN $ids RETURN count(e) AS n", v=layer, ids=list(case_ids)).single()["n"]
        if leaked:
            raise M.Leak(f"시험 사례 기록 {leaked}건이 그래프에 원인과 함께 있습니다")
        self.queries = 0

    def _alarm_hit(self, s, rule, base):
        p = dict(base, n=rule["at_least"])
        if rule["rel"] == "same_line":
            q = ALARM_SAME_LINE.replace("{where}", _where(rule, "r", p))
        elif rule["rel"] == "machine_work":
            q = ALARM_MACHINE_WORK.replace("{where}", _where(rule, "r", p))
        else:
            q = ALARM_SELF.replace("{where}", _where(rule, "e", p))
        self.queries += 1
        return s.run(q, p).single()["hit"]

    def _evidence(self, s, rule, base):
        p = dict(base, n=rule["at_least"])
        frag = {"self": EV_SELF, "same_line": EV_SAME_LINE, "machine_work": EV_MACHINE_WORK}[rule["rel"]]
        cond = frag.replace("{where}", _where(rule, "e" if rule["rel"] == "self" else "r", p))
        self.queries += 1
        rows = s.run(EVIDENCE.replace("{cond}", cond), p).data()
        return {r["cause"]: r["w"] for r in rows}, sum(r["n"] for r in rows)

    def run(self, k, alarm, history=None):
        from load_graph import epoch
        t = epoch(alarm["ts"])
        season = {m: x for x, ms in self.defs["season"].items() for m in ms}[int(alarm["ts"][5:7])]
        flat = {"season": season, "machine": alarm["machine"], "line": alarm["line"], **alarm["sensors"]}
        base = {"v": self.v, "t": t, "line": alarm["line"], "machine": alarm["machine"], "alarm": flat, "vis": VISIBLE_S,
                "win": self.defs["same_line"]["window_min"], "days": self.defs["machine_work"]["max_days"],
                "scope": self.opt["scope"], "h": self.opt["half_life_days"]}
        with self.drv.session() as s:
            for rule in self.rules:
                if not self._alarm_hit(s, rule, base):
                    continue
                counts, n_ev = self._evidence(s, rule, base)
                out = N.decide(k, rule, counts, n_ev, self.opt["min_support"])
                if out:
                    return out
        return {**M.manual_only(k, alarm, None), "used": "manual", "support": 0}

    def close(self):
        self.drv.close()
