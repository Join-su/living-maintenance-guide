"""관계표 씀(v0) · D-531 세 방식 — 알람 한 건 + 그때까지의 이력 → 불러온 점검 · 조치 · 호출 목록(순서 = 할 순서, 첫째 = 첫 단계).

1 manual_only: 냉각 장의 점검 · 조치 · 호출 전부(점검 → 조치 → 호출, 각 안은 소요 분 순).
2 similarity: 지금 retrieve@2 꼴(packrt 0.15.0 FlatLocal.search2 그대로 · BM25 + 센서 재정렬 · 벡터 0) 상위 10건 → 원인 몫(지금 Pack R4: α 0.5 + 점수 합).
3 relation: 그때까지의 이력에서 관계를 셈 — 같은 라인 30분 안 동시 · 같은 설비 3일 안 부품 교체 · 같은 설비 14 ~ 21일 전 필터 늦은 교체 ·
  센서 모순 · 계절 분포 → 원인 몫.
2 · 3의 '원인 몫 → 불러올 것'은 select() 하나(사람이 정한 관계 표 relations/)."""
import csv
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from packrt.index.build import rerank_norm
from packrt.run.backends import FlatLocal

HERE = Path(__file__).resolve().parent
FMT = "%Y-%m-%dT%H:%M"
FIELDS = ["air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min", "temp_diff_k", "power_w"]
QUERY_TEXT = "온도 차 저하 경계 아래"   # 지금 Pack history_query 기본 꼴(증상군 이름 + 방향)
ALPHA = 0.5          # 지금 Pack R4
SHARE_MIN = 0.2      # 불러올 원인 몫 아래 끝
OUTSIDE_ONLY = 0.5   # 매뉴얼 밖 원인 몫이 이 위면 호출 · 인계만
MIN_SUPPORT = 5      # 관계 몫을 쓰려면 그때까지 이력에서 같은 관계 기록이 이만큼 있어야 함
MODE = {"A": "단독", "B": "승인 요청", "C": "파트장 지시"}


V0 = "> **관계표 씀(v0)** — 사람이 정한 관계 표(relations/check_cause.csv · cause_step.csv)를 읽은 방식의 결과입니다. 관계표 없는 비교는 compare3/(세 줄)."   # 이 방식 결과 머리 줄(D-540)

class Leak(ValueError):
    """시험 사례 기록이 이력에 들어 있거나, 알람 시각 뒤 기록을 보려 할 때."""


def _t(s):
    return datetime.strptime(s, FMT)


class Knowledge:
    """매뉴얼 절(rules.yaml steps = manual/cooling.md 머리) + 사람이 정한 관계 표."""

    def __init__(self, rules_path=HERE / "rules.yaml", rel=HERE / "relations"):
        r = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8"))
        self.steps = r["steps"]
        self.boundary = r["sensors"]["alarm"]["below"]
        self.seasons = r["plant"]["seasons"]
        self.check_of = {row["cause"]: row["check"] for row in csv.DictReader((rel / "check_cause.csv").open(encoding="utf-8"))}
        self.step_of = {row["cause"]: row["step"] for row in csv.DictReader((rel / "cause_step.csv").open(encoding="utf-8"))}
        self.causes = sorted(self.step_of)

    def season(self, ts):
        return next(s for s, ms in self.seasons.items() if ts.month in ms)

    def plan(self, cause):
        return ([self.check_of[cause]] if cause in self.check_of else []) + [self.step_of[cause]]

    def item(self, sec):
        st = self.steps[sec]
        return {"section": sec, "kind": st["kind"], "grade": st["grade"], "mode": MODE[st["grade"]]}


class History:
    """그때까지의 이력 — 기록(끝난 것: t − 2시간 전) · 알람 발생(t 전)."""

    def __init__(self, records, occurrences, case_ids=()):
        leaked = sorted(set(case_ids) & {r["record_id"] for r in records})
        if leaked:
            raise Leak(f"시험 사례 기록 {len(leaked)}건이 이력에 있습니다")
        self.records = sorted(records, key=lambda r: (r["ts"], r["record_id"]))
        self.occ = sorted(occurrences, key=lambda r: (r["ts"], r["record_id"]))
        for r in self.records + self.occ:
            r["_t"] = _t(r["ts"])

    def view(self, t):
        return [r for r in self.records if r["_t"] < t - timedelta(hours=2)], [o for o in self.occ if o["_t"] < t]


def select(k, probs, outside):
    """원인 몫 → 불러올 단계(방식 2 · 3 같은 규칙). 매뉴얼 밖 원인 몫 ≥ 0.5면 그것만. 아니면 몫 ≥ 0.2 원인 모두(적어도 1등).
    순서 = 원인 몫 ÷ 그 원인 첫 단계 소요 분(지금 Pack R5 꼴), 원인 안에서는 점검 → 조치."""
    top = max(sorted(probs), key=lambda c: probs[c])
    chosen = [top] if (top in outside and probs[top] >= OUTSIDE_ONLY) else [c for c in sorted(probs) if probs[c] >= SHARE_MIN] or [top]
    chosen.sort(key=lambda c: (-probs[c] / k.steps[k.plan(c)[0]]["minutes"], c))
    out, seen = [], set()
    for c in chosen:
        for sec in k.plan(c):
            if sec not in seen:
                seen.add(sec)
                out.append(k.item(sec))
    return out


def _norm(scores):
    s = sum(scores.values())
    return {c: v / s for c, v in scores.items()}


def manual_only(k, alarm, history):
    order = {"check": 0, "action": 1, "call": 2}
    secs = sorted(k.steps, key=lambda s: (order[k.steps[s]["kind"]], k.steps[s]["minutes"], [int(x) for x in s.split(".")]))
    return {"steps": [k.item(s) for s in secs], "probs": None}


def _text(r):
    checks = " ".join(f"{c['section']} {c['result']}" for c in r["check_results"])
    acts = " ".join(f"{a['section']} {a.get('note', '')}".strip() for a in r["actions"])
    return f"온도 차 저하 · 점검 {checks} · 원인 {r['cause_name']} · 조치 {acts} · 메모 {r['memo']}"


def similarity(k, alarm, history, pool=None):
    """packrt FlatLocal.search2(method lexical · rerank 센서 7칸 0.5/0.5 · top_k 10) — 지금과 다른 점: 벡터 순위 0(질의 임베딩 = provider 호출)."""
    recs, _ = history.view(_t(alarm["ts"]))
    chunks = [{"id": r["record_id"], "text": _text(r), "locator": {"record_id": r["record_id"]},
               "metadata": {"symptom_group": r["group"], "cause": r["cause"], **r["sensors"]}} for r in recs]
    store = FlatLocal.__new__(FlatLocal)
    store.chunks, store.vectors = chunks, {}
    rr = {"fields": FIELDS, "weights": {"retrieval": 0.5, "sensor": 0.5}}
    if pool:
        rr["pool"] = pool
    p = {"method": "lexical", "lexical": {"tokenizer": "ko-bigram-1", "k1": 1.5, "b": 0.75}, "rerank": rr, "top_k": 10, "return_metadata": ["cause"]}
    norm = rerank_norm(chunks, {"from": {"symptom_group": alarm["group"]}, "fields": FIELDS})
    hits = store.search2(None, QUERY_TEXT, p, {"symptom_group": alarm["group"]}, norm, alarm["sensors"])
    scores = {c: ALPHA for c in k.causes}
    for h in hits:
        if h["metadata"]["cause"] in scores:
            scores[h["metadata"]["cause"]] += h["score"]
    probs = _norm(scores)
    outside = {r["cause"] for r in recs if not r["in_manual"]}
    return {"steps": select(k, probs, outside), "probs": probs, "hits": [h["locator"]["record_id"] for h in hits]}


# --- 방식 3: 관계 질의(이력에서 셈) ---
def relation_flags(k, ev, recs_by_machine, occ_by_line, t):
    """알람 ev(시각 t)에서 참인 관계 — 이력은 t에 보이는 것만 받음."""
    same_line = [o for o in occ_by_line.get(ev["line"], []) if o["machine"] != ev["machine"] and t - timedelta(minutes=30) <= o["_t"] < t]
    mine = recs_by_machine.get(ev["machine"], [])
    replaced = [r for r in mine if t - timedelta(days=3) <= r["_t"] < t and any(a["section"] in ("5.1", "5.2") and not a.get("note") for a in r["actions"])]
    late = [r for r in mine if t - timedelta(days=21) <= r["_t"] <= t - timedelta(days=14) and any(a.get("note") for a in r["actions"])]
    s = ev["sensors"]
    contra = s["temp_diff_k"] < k.boundary and s["rotational_speed_rpm"] >= 1450   # 온도 차만 낮고 회전수는 정상 — 센서 모순
    return {"line_concurrent": len(same_line) >= 2, "recent_replacement": bool(replaced), "late_filter": bool(late), "sensor_contradiction": contra}


def _index(recs, occ):
    by_m, by_l = {}, {}
    for r in recs:
        by_m.setdefault(r["machine"], []).append(r)
    for o in occ:
        by_l.setdefault(o["line"], []).append(o)
    return by_m, by_l


def relation(k, alarm, history):
    t = _t(alarm["ts"])
    recs, occ = history.view(t)
    by_m, by_l = _index(recs, occ)
    flags = relation_flags(k, {**alarm, "_t": t}, by_m, by_l, t)
    # 같은 관계가 참이던 지난 기록들의 확정 원인 분포(그 기록의 시각에서 셈 · 그때 보이던 것만)
    dist = {}
    on = [f for f, v in flags.items() if v]
    if on:
        for r in recs:
            rr_recs = [x for x in by_m.get(r["machine"], []) if x["_t"] < r["_t"] - timedelta(hours=2)]
            rr_occ = [o for o in by_l.get(r["line"], []) if o["_t"] < r["_t"]]
            f = relation_flags(k, r, {r["machine"]: rr_recs}, {r["line"]: rr_occ}, r["_t"])
            for name in on:
                if f[name]:
                    dist.setdefault(name, {}).setdefault(r["cause"], 0)
                    dist[name][r["cause"]] += 1
    usable = {n: d for n, d in dist.items() if sum(d.values()) >= MIN_SUPPORT}
    if usable:   # 가장 한쪽으로 쏠린 관계 하나(지난 기록에서 1등 원인 비율이 가장 큼)
        name = max(sorted(usable), key=lambda n: max(usable[n].values()) / sum(usable[n].values()))
        counts, used = usable[name], name
    else:        # 관계 없음 → 같은 계절 원인 분포
        s = k.season(t)
        counts = {}
        for r in recs:
            if k.season(r["_t"]) == s:
                counts[r["cause"]] = counts.get(r["cause"], 0) + 1
        used = f"season:{s}"
    probs = _norm({c: ALPHA + counts.get(c, 0) for c in k.causes})
    outside = {r["cause"] for r in recs if not r["in_manual"]}
    return {"steps": select(k, probs, outside), "probs": probs, "flags": flags, "used": used}


METHODS = {"1 매뉴얼만": manual_only, "2 유사도(지금 꼴)": similarity, "2′ 유사도(재정렬 범위 전체)": lambda k, a, h: similarity(k, a, h, pool=5000),
           "3 관계 질의": relation}
