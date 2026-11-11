"""D-533 관계 질의 노드(알람 실행 안 · 지금 이력 리트리버 자리) — 관계 층(build_layer.py)을 읽고, 예외 규칙 표를 찾을 때 적용한다.

옵션 셋(node.yaml · 일반적인 것만):
  scope 근거 범위 — machine 같은 설비 · line 같은 라인 · plant 같은 기종(이 가상 공장 30대 = 한 기종)
  half_life_days 최근 기록 무게 — 며칠마다 무게 반(null = 같은 무게)
  min_support 최소 근거 수 — 모자라면 매뉴얼 순서
예외 = 규칙 표(exceptions.csv · 한 줄 = 한 예외 · 사람이 정한 숫자 · 위 줄부터):
  알람에서 조건이 참 → 범위 안 지난 기록 중 같은 조건이 참이던 기록(근거)을 셈 → 근거 ≥ min_support이고 근거의 1등 원인 = 결론이면
  근거의 원인 몫을 씀('분포'는 1등 확인 없이). 맞는 줄이 없으면 매뉴얼 순서.
새 예외 = 규칙 표 한 줄(있는 관계 rel · 칸만 쓰면 노드 · 옵션 · 관계 층 그대로). 새 관계가 필요하면 = 관계 정의 바꿈 → 관계 층 새 버전."""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

import build_layer as B
import methods as M

HERE = Path(__file__).resolve().parent
SCOPES = ("machine", "line", "plant")
OPTION_KEYS = {"scope", "half_life_days", "min_support"}
RELS = ("self", "same_line", "machine_work")
VISIBLE = timedelta(hours=2)   # 기록은 끝나고 2시간 뒤부터 보임(rules.yaml visibility · 셋 방식 같음)
OPS = ("<=", ">=", "<", ">", "=")


class RuleError(ValueError):
    """규칙 표 줄이 모르는 관계 · 칸 · 연산을 씀."""


def _parse_where(s):
    out = []
    for part in filter(None, (s or "").split(";")):
        op = next((o for o in OPS if o in part), None)
        if op is None:
            raise RuleError(f"연산 없음: {part}")
        f, v = part.split(op, 1)
        out.append((f.strip(), op, v.strip()))
    return out


def load_rules(path=HERE / "exceptions.csv", causes=()):
    with Path(path).open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["rel"] not in RELS:
            raise RuleError(f"{r['id']}: 모르는 관계 {r['rel']}")
        if r["then"] != "분포" and causes and r["then"] not in causes:
            raise RuleError(f"{r['id']}: 모르는 결론 {r['then']}")
        r["where"], r["at_least"] = _parse_where(r["where"]), int(r["at_least"])
    return rows


def _cmp(a, op, b):
    if a is None:
        return False
    if op == "=":
        return str(a) == str(b)
    a, b = float(a), float(b)
    return {"<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b}[op]


def _value(item, node, f):
    if f in item:
        return item[f]
    if f in node.get("sensors", {}):
        return node["sensors"][f]
    if f in node:
        return node[f]
    raise RuleError(f"모르는 칸: {f}")


def holds(rule, node, edges, ref):
    """규칙 조건이 node(그 노드의 관계 edges)에서 참인가. 값 '@' = 지금 알람(ref)의 같은 칸."""
    def ok(item):
        return all(_cmp(_value(item, node, f), op, _value(ref, ref, f) if v == "@" else v) for f, op, v in rule["where"])
    if rule["rel"] == "self":
        return ok(node)
    return sum(1 for e in edges if e["rel"] == rule["rel"] and ok(e)) >= rule["at_least"]


class Layer:
    """관계 층 한 버전(읽기만). 원본 층 sha256이 manifest와 다르면 멈춤(다른 원본으로 만든 층)."""

    def __init__(self, path, raw=None, case_ids=()):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
        self.defs = yaml.safe_load((self.path / "defs.yaml").read_bytes())
        if raw is not None:
            got = {f: B._sha((Path(raw) / f).read_bytes()) for f in B.RAW}
            if got != self.manifest["raw_sha256"]:
                raise ValueError("관계 층이 이 원본 층으로 만든 것이 아닙니다(sha256 다름)")
        self.nodes = B._rows(self.path / "nodes.jsonl")
        leaked = sorted(set(case_ids) & {n["id"] for n in self.nodes if n["cause"]})
        if leaked:
            raise M.Leak(f"시험 사례 기록 {len(leaked)}건이 관계 층에 원인과 함께 있습니다")
        self.idx = B.Index(self.nodes)
        self.edges = {}
        for e in B._rows(self.path / "edges.jsonl"):
            self.edges.setdefault(e["from"], []).append(e)


def visible(edges):
    """그 노드 시각에 보이던 관계만 — 앞 기록은 2시간 지나야 보임(앞 알람 발생은 바로 보임)."""
    return [e for e in edges if e["rel"] != "machine_work" or e["age_days"] * 86400 >= VISIBLE.total_seconds() + 1e-6]


class Node:
    def __init__(self, layer, rules, options, k):
        if set(options) != OPTION_KEYS:
            raise ValueError(f"옵션은 {sorted(OPTION_KEYS)} 셋만")
        if options["scope"] not in SCOPES:
            raise ValueError(f"scope는 {SCOPES} 중 하나")
        self.layer, self.rules, self.opt, self.k = layer, rules, options, k

    @classmethod
    def from_config(cls, k, config=HERE / "node.yaml", raw=None, case_ids=()):
        c = yaml.safe_load(Path(config).read_text(encoding="utf-8"))
        base = Path(config).parent
        layer = Layer(base / c["layer"], raw=raw, case_ids=case_ids)
        return cls(layer, load_rules(base / c["rules"], causes=k.causes), c["options"], k)

    def _in_scope(self, n, alarm):
        s = self.opt["scope"]
        return s == "plant" or n[s] == alarm[s]

    def _weight(self, n, t):
        h = self.opt["half_life_days"]
        return 1.0 if not h else 0.5 ** ((t - n["_t"]).total_seconds() / 86400 / h)

    def run(self, k, alarm, history=None):
        """알람 한 건 → 불러올 점검 · 조치 · 호출(방식 1 · 2 · 3과 같은 꼴). history는 받지 않고 관계 층만 읽음."""
        t = datetime.strptime(alarm["ts"], M.FMT)
        defs = self.layer.defs
        ref = {**alarm, "id": alarm["record_id"], "season": {m: s for s, ms in defs["season"].items() for m in ms}[t.month]}
        mine = visible(B.edges_for(ref, self.layer.idx, defs))   # 새 알람의 관계 = 관계 층을 만든 같은 함수 · 같은 정의
        past = [n for n in self.layer.nodes if n["cause"] and n["_t"] < t - VISIBLE and self._in_scope(n, alarm)]
        for rule in self.rules:
            if not holds(rule, ref, mine, ref):
                continue
            counts = {}
            n_ev = 0
            for n in past:
                if holds(rule, n, visible(self.layer.edges.get(n["id"], [])), ref):
                    counts[n["cause"]] = counts.get(n["cause"], 0.0) + self._weight(n, t)
                    n_ev += 1
            out = decide(k, rule, counts, n_ev, self.opt["min_support"])
            if out:
                return out
        return {**M.manual_only(k, alarm, None), "used": "manual", "support": 0}


def decide(k, rule, counts, n_ev, min_support):
    """근거(원인별 무게 합 · 근거 수) → 쓸지 · 원인 몫 · 불러올 단계. 파일 노드 · 그래프 노드가 같이 씀."""
    if n_ev < min_support:
        return None
    if rule["then"] != "분포" and max(sorted(counts), key=lambda c: counts[c]) != rule["then"]:
        return None
    probs = M._norm({c: M.ALPHA + counts.get(c, 0.0) for c in k.causes})
    outside = {c for c in k.causes if k.steps[k.step_of[c]]["kind"] == "call"}   # 매뉴얼 밖 원인 = 관계 표에서 호출 · 인계로 가는 원인
    return {"steps": M.select(k, probs, outside), "probs": probs, "used": rule["id"], "support": n_ev}
