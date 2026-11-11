"""C1-a15 Pack 그래프 질의 node(custom_code@3 · D-536 ④) — 이력 리트리버 자리와 판정이 읽는 원인–점검–조치 관계를 그래프 release 하나에서.

그래프 release = 적재기(kg-test/graph-v3/loader.py)가 그래프 DB 꼬리표를 파일로 내보낸 것 — 자리는 templates/graph/current.json 하나(이름 고정 · D-545 ·
layer · release_id는 파일 안 칸 · LLMOps 고침 길(packrt patch)이 바꿀 수 있는 자리) · 망 0 · 표준 라이브러리만. 꼴 정본 = handoff/d545/graph-shape.md.
관계(이상 나온 점검 → 원인 · 원인 → 조치 · 증상군 → 원인 후보)는 관계표 없는 매뉴얼 MG-MM 3.0 + 승인된 정비 이력에서 센 값.
절 번호는 앱이 쓰는 옛 번호(release의 app_no 칸)로 냄 — 출력 꼴 = 약속 v6 그대로.

옵션 셋(D-533 꼴): scope 근거 범위(group 같은 증상군 · all 전체 · equipment 같은 증상군의 같은 설비 — 약속 v7 equipment_id, 없으면 group) ·
half_life_days 최근 기록 무게(0 = 무게 없음 · 기준 시각 = 약속 v7 occurred_at, 없으면 이력 마지막 기록) · min_support 최소 근거 수
(못 넘으면 고른 기록 0 → 판정은 매뉴얼 순서). 예외 = 규칙 표(custom/graph/rules.csv · 찾을 때 적용 · 그래프에 저장 0) — release의 노하우 줄을 그 앞에 읽음.
occurred_at이 있으면 그 시각 앞선 기록만 근거. 규칙 관계는 아직 self(알람 센서 칸)만 받음(같은 설비 · 라인 조건 줄은 ⑥)."""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from custom import exception, knowledge
from custom.exception import FIELDS, OPS, RuleError  # noqa: F401 — 규칙 표 · 예외 관계가 같은 칸 목록 · 연산 · 오류를 씀

HERE = Path(__file__).with_name("graph")   # 규칙 표(rules.csv) 자리
CURRENT = Path(__file__).resolve().parents[1] / "templates/graph/current.json"   # Pack이 쓰는 그래프(바꿔 끼움 = 이 파일 · 옛 버전은 git · LLMOps 버전 기록)
OPTION_KEYS = {"scope", "half_life_days", "min_support"}
SCOPES = ("group", "all", "equipment")
DEFAULT = {"scope": "group", "half_life_days": 0, "min_support": 3}


@lru_cache(maxsize=4)
def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def release(path=CURRENT):
    """그래프 release(한 경로 = 한 캐시 칸 — 부르는 꼴과 상관없이 같은 값)."""
    return _read(str(Path(path).resolve()))


release.cache_clear = _read.cache_clear


def _where(text):
    out = []
    for part in [p.strip() for p in (text or "").split(";") if p.strip()]:
        m = re.fullmatch(r"([a-z_]+)\s*(>=|<=|=|>|<)\s*(.+)", part)
        if not m or m.group(1) not in FIELDS:
            raise RuleError(f"규칙 조건 칸 이름이 허용 목록 밖: {part.split(' ')[0]}")
        out.append((m.group(1), m.group(2), float(m.group(3))))
    return out


def load_rules(path=HERE / "rules.csv", causes=()):
    """규칙 표(id,name,rel,where,then,basis — 쉼표 없는 칸). rel은 self만 · then은 release 원인 코드 또는 분포."""
    lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    head = lines[0].split(",")
    if head != ["id", "name", "rel", "where", "then", "basis"]:
        raise RuleError("규칙 표 머리 칸이 다름")
    return [_rule(dict(zip(head, l.split(","))), causes) for l in lines[1:]]


def _rule(r, causes):
    if r.get("rel") != "self":
        raise RuleError(f"{r.get('id')}: start 요청에서 볼 수 없는 관계(설비 · 시각 칸 없음)")
    if r["then"] != "분포" and r["then"] not in causes:
        raise RuleError(f"{r.get('id')}: 그래프에 없는 원인")
    return {**r, "where": _where(r["where"])}


def knowhow_rules(rel):
    """release의 노하우 줄(llmops-2 설계 126) → 규칙 꼴. 근거 부족(낮은 무게) 줄은 고르기에 쓰지 않음(순서 바꿈 0)."""
    causes = {c["code"] for c in rel["causes"]}
    check_cause = {x["no"]: x["code"] for x in rel["indicates"]}
    out = []
    for k in rel.get("knowhow", []):
        if k.get("weight") == "low":
            continue
        then = check_cause.get(k.get("then"), k.get("then"))
        out.append(_rule({"id": f"KH-{k.get('knowhow_id')}", "name": "노하우", "rel": k.get("rel"), "where": k.get("where"), "then": then, "basis": k.get("text_sha256", "")}, causes))
    return out


def check_options(o):
    o = {**DEFAULT, **(o or {})}
    extra = set(o) - OPTION_KEYS
    if extra:
        raise ValueError(f"그래프 질의 옵션은 셋뿐: {sorted(extra)}")
    if o["scope"] not in SCOPES:
        raise ValueError("근거 범위 값이 다름")
    return o


def _scale(recs):
    s = {}
    for f in FIELDS:
        v = [r["sensors"][f] for r in recs]
        m = sum(v) / len(v)
        s[f] = math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1.0
    return s


_ts = exception._secs   # 시각 → UTC 초(시간대 칸을 뺌 — 약속 v7 occurred_at은 다른 시간대일 수 있음) · 시각 읽기는 한 곳(D-585)


def query(request, history_query, options=None, path=CURRENT, rules_path=HERE / "rules.csv"):
    """비슷한 기록 상위 10(조각 꼴 = retrieve@2 Hit와 같은 칸: locator.record_id · metadata.cause · score · index_release)."""
    o = check_options(options)
    rel = release(path)
    recs = rel["records"]
    group = history_query["group"]
    scope = o["scope"] if o["scope"] != "equipment" or history_query.get("equipment_id") else "group"   # 설비 칸이 없으면 같은 증상군
    at = _ts(history_query["occurred_at"]) if history_query.get("occurred_at") else None
    pool = [r for r in recs if (scope == "all" or r["group"] == group) and (scope != "equipment" or r["equipment"] == history_query["equipment_id"])
            and (at is None or _ts(r["ts"]) < at)]
    alarm = {f: history_query["sensors"][f] for f in FIELDS}
    used = None
    for rule in knowhow_rules(rel) + load_rules(rules_path, {c["code"] for c in rel["causes"]}):
        if not all(OPS[op](alarm[f], v) for f, op, v in rule["where"]):
            continue
        ev = [r for r in pool if all(OPS[op](r["sensors"][f], v) for f, op, v in rule["where"])]
        top = max(sorted({r["cause"] for r in ev}), key=lambda c: sum(1 for r in ev if r["cause"] == c)) if ev else None
        if len(ev) >= o["min_support"] and (rule["then"] == "분포" or top == rule["then"]):
            pool, used = ev, rule["id"]
            break
    if len(pool) < o["min_support"]:
        return {"history_hits": []}
    sc = _scale(recs)
    latest = at if at is not None else max(_ts(r["ts"]) for r in recs)
    hits = []
    for r in pool:
        d = math.sqrt(sum(((alarm[f] - r["sensors"][f]) / sc[f]) ** 2 for f in FIELDS) / len(FIELDS))
        w = 0.5 ** ((latest - _ts(r["ts"])) / 86400 / o["half_life_days"]) if o["half_life_days"] else 1.0
        hits.append({"locator": {"record_id": r["id"]}, "metadata": {"cause": r["cause"]}, "score": round(w / (1 + d), 6),
                     "index_release": rel["release_id"], "ranks": {}, "fallback_used": False, "graph": {"used": used or "base", "layer": rel["layer"], "scope": scope}})
    hits.sort(key=lambda h: (-h["score"], h["locator"]["record_id"]))
    return {"history_hits": hits[:10]}


def cause_evidence(group, path=CURRENT):
    """증상군(코드) → {원인 코드: 그래프 근거 건수(CANDIDATE n)} — 판정 순서 신호 evidence(custom.judge)."""
    rel = release(path)
    name = next(g["name"] for g in rel["groups"] if g["code"] == group)
    return {x["code"]: x["n"] for x in rel["candidate"] if x["group"] == name}


KIND = {"점검": "check", "사전 확인": "precheck", "조치": "action", "공통": "common", "재가동": "common", "마감": "common", "예외": "exception"}


def scope_counts(group, equipment_id=None, occurred_at=None, min_support=DEFAULT["min_support"], path=CURRENT):
    """정본 확률 재료(D-543): 범위 안 관계 있는 이력(원인 확정 기록)의 원인별 개수. 범위 = 같은 증상군 · 알람 시각 앞선 기록,
    설비가 오면 같은 설비로 좁힘(그 설비 기록이 최소 근거 수보다 적으면 같은 증상군 그대로)."""
    return scope_basis(group, equipment_id, occurred_at, min_support, path)[1]


def scope_basis(group, equipment_id=None, occurred_at=None, min_support=DEFAULT["min_support"], path=CURRENT):
    """(쓴 범위 'equipment' | 'group', 원인별 개수) — D-642 결과 record_counts(앱 '이 설비 기록 n건 중 k건')."""
    at = _ts(occurred_at) if occurred_at else None
    recs = [r for r in release(path)["records"] if r["group"] == group and (at is None or _ts(r["ts"]) < at)]
    scope = "group"
    if equipment_id:
        eq = [r for r in recs if r["equipment"] == equipment_id]
        if len(eq) >= min_support:
            recs, scope = eq, "equipment"
    return scope, _by_cause(recs)


def _by_cause(recs):
    out = {}
    for r in recs:
        out[r["cause"]] = out.get(r["cause"], 0) + 1
    return out


def exception_relation(rel_id, path=CURRENT):
    """관계 id → (관계 줄, 예외 절 줄) 또는 None — custom.exception.find."""
    return exception.find(release(path), rel_id)


def exception_match(group, sensors, equipment_id=None, path=CURRENT, occurred_at=None, recent_work=None):
    """알람 → 맞는 예외 안내 관계 하나 — custom.exception.match(알람 시각 · 앱이 보낸 그 설비 최근 정비 = 최근 정비 조건 기준 · D-570)."""
    return exception.match(release(path), group, sensors, equipment_id, occurred_at, recent_work)


def check_desc(path=CURRENT):
    """D-560: 점검 절 설명 {app_no: desc} — 적재 때 매뉴얼 판정 기준 첫 문장(LLM 0) · 설명 없는 그래프(mm3-v2 이전)면 빈 표."""
    return {s["app_no"]: s["desc"] for s in release(path)["sections"] if s.get("app_no") and s.get("desc")}


def with_desc(decision, path=CURRENT):
    """결정의 후보(reason_facts.alternatives)마다 그 절 설명 desc를 실음(없는 절은 칸 0) · 후보 없는 결정은 그대로."""
    rf = decision.get("reason_facts") if isinstance(decision, dict) else None
    alts = rf.get("alternatives") if isinstance(rf, dict) else None
    if not alts:
        return decision
    desc = check_desc(path)
    return {**decision, "reason_facts": {**rf, "alternatives": [{**a, **({"desc": desc[a["section"]]} if a.get("section") in desc else {})} for a in alts]}}


def knowledge_from_graph(path=CURRENT):
    """판정이 읽는 매뉴얼 지식 — 모두 그래프 release에서(D-543 · Pack 안 매뉴얼 파일 읽기 0).
    절(앱이 쓰는 옛 번호 app_no) · 제목 · 종류 · 증상군 · 등급 · 분 · 시작 전 체크리스트 = 절 노드 · 사전 확인 = 증상군의 사전 확인 절 노드 ·
    원인 후보 · 점검 → 원인(선별 · 확정) · 원인 → 조치 = 이력에서 센 관계(candidate · indicates · remedied_by)."""
    rel = release(path)
    code_of = {g["name"]: g["code"] for g in rel["groups"]}
    by_id = {s["app_no"]: {"id": s["app_no"], "title": s["name"], "kind": KIND[s["kind"]], "group": code_of.get(s["group"]),
                           "grade": s["grade"], "minutes": s["minutes"], "checklist": tuple(s.get("checklist") or ())}
             for s in rel["sections"] if s["app_no"]}
    cause_group = {}
    for x in rel["candidate"]:
        if x["n"] > cause_group.get(x["code"], ("", 0))[1]:
            cause_group[x["code"]] = (code_of[x["group"]], x["n"])
    k = knowledge.ManualKnowledge(sections=by_id)
    best_check = {}
    for x in rel["indicates"]:
        if x["n"] > best_check.get(x["app_no"], ("", 0, ""))[1]:
            best_check[x["app_no"]] = (x["code"], x["n"], x["role"])
    for sec, (code, _, role) in sorted(best_check.items(), key=lambda t: knowledge.section_key(t[0])):
        s = by_id[sec]
        k.checks.setdefault(s["group"], []).append(knowledge.Check(sec, s["title"], s["group"], code, role, s["checklist"]))
    for s in sorted((s for s in by_id.values() if s["kind"] != "exception"), key=lambda s: knowledge.section_key(s["id"])):   # 예외 절(EX-…)은 번호 정렬 밖
        if s["kind"] == "precheck":
            k.prechecks[s["group"]] = s["id"]
    names = {c["code"]: c["name"] for c in rel["causes"]}
    for code in sorted(cause_group):
        conf = [x["app_no"] for x in rel["indicates"] if x["code"] == code and x["role"] == "confirm"]
        acts = sorted((x for x in rel["remedied_by"] if x["code"] == code), key=lambda x: (-x["n"], x["app_no"]))
        k.causes.setdefault(cause_group[code][0], []).append(
            knowledge.Cause(code, names[code], cause_group[code][0], conf[0] if conf else "", acts[0]["app_no"] if acts else ""))
    return k


def node(request, history_query, options=None):
    """pack.yaml graph_query node — 옵션 셋을 받아 비슷한 기록 상위 10(history_hits)."""
    return query(request, history_query, options)
