"""C1-a15 Pack 판정 node(custom_code@3) — 요청 본문만 읽고 다음 단계 하나를 돌려준다. 망 · 부작용 · 앱 import 0.

판정은 C1-a15 앱 engine/의 fold → decide 사본(custom/session.py · rules.py · knowledge.py, 출처 COPIED_FROM.json)이 한다.
매뉴얼 지식은 그래프 release(templates/graph/current.json · D-543 · D-545 — 매뉴얼 파일 읽기 0), 판정 시점 등급표 · 소요 분은 요청 본문(R13).
옵션(D-476, packrt params.options — pack.yaml이 선언 · 지금 값이면 v15와 같은 출력): ① query_text(이력 질의 글 꼴) ·
③ score_rule · near_min · base_score · call_threshold(판정 셈) · ④ use_manual · manual_weight(매뉴얼 결과를 판정에) ·
⑤ order_signal(원인 순서 신호 — similar 비슷한 10건 몫(코드 기본 · 지금) · evidence 그래프 근거 건수 몫(D-540) ·
count_group · count_equipment 범위 안 개수 비율(D-543 정본 확률 — 같은 증상군 · 알람 시각 앞선 기록 / 같은 설비로 좁힘)).
안전 규칙(잠금 먼저 · A · B만 지시 · C · D 호출 · 확인 불가 2회 · 사전 점검)은 옵션 밖 — 사본 session.decide 그대로.
예외 안내(D-545 · 약속 v8): 그래프 exception_relations 줄이 알람에 맞으면 첫 점검 차례에 예외 절(EX-…)을 먼저(C → 파트장 승인 → 결과 —
정상이면 원래 순서 다음 · 이상이면 조치 → 재가동 → 보고서). 예외 절 사건은 사본 session에 넘기지 않고 여기서만 셈(사본 손 0).
점검 바꾸기 · 예외 진행(D-556 · 약속 v9): judge 결정에 custom/switch.py 덧층(사본 손 0)."""
import dataclasses
from functools import lru_cache

from custom import alarm, alarm_rules, exception, graph_query, knowledge, order as order_overlay, rules, session, switch


@lru_cache(maxsize=1)
def manual() -> knowledge.ManualKnowledge:
    """D-543(dev-2): 판정 지식은 그래프 release 하나에서(custom.graph_query) — 절 · 제목 · 사전 확인 · 체크리스트 · 원인 후보 ·
    점검 → 원인 · 원인 → 조치. Pack 안 매뉴얼 파일(manual_sections.json) 읽기 0 · 판정 시점 등급표 · 소요 분은 요청 본문(R13)."""
    return graph_query.knowledge_from_graph()


GROUP_NAME = {"TW": "공구 마모 경고", "PW": "출력 이탈", "CL": "온도 차 저하", "LD": "토크 과부하"}   # 앱 api/sessions.py GROUP_NAME(7b79da9:52) 사본 — 질의 글을 앱 R3와 같게


def scoring(options, support):
    """판정 셈 옵션 → 사본 session.Scoring(옵션 없음 = Scoring() = 지금 셈). support = {원인: 매뉴얼 몫} 또는 None."""
    o = options or {}
    use = bool(o.get("use_manual")) and support is not None
    return session.Scoring(alpha=o.get("base_score", rules.ALPHA), rule=o.get("score_rule", "sum"), near_min=o.get("near_min", 0.8),
                           manual=support if use else None, manual_weight=o.get("manual_weight", 0.0) if use else 0.0)


ORDER_SIGNALS = ("similar", "evidence", "count_group", "count_equipment")
PRIOR = 1   # D-642 A: count_equipment 셈에 원인마다 기본 1건
NO_ZERO = 1e-9   # 개수 0인 원인만 남았을 때 나눗셈 0 막음(확률 표시는 0%) — 모두 0이면 매뉴얼 순서


def evidence_scoring(k, group, similar, sc):
    """⑤ evidence: 비슷한 10건이 주던 몫 크기(셈 꼴 그대로 · α 뺀 합)는 그대로, 원인끼리 나누는 기준만 그래프 근거 건수(release candidate n).
    사본 session · rules는 손 0 — 몫은 사본의 매뉴얼 몫 자리(manual · 무게 1)로 넣고, 매뉴얼 결과(④)를 켰으면 그 몫을 더함."""
    cands = [c.code for c in rules.ordered_causes(k, group)]
    mass = sum(rules.cause_scores(cands, similar, 0.0, sc.rule, sc.near_min).values())
    prior = graph_query.cause_evidence(group)
    tot = sum(prior.get(c, 0) for c in cands)
    share = {c: mass * prior.get(c, 0) / tot if tot else 0.0 for c in cands}
    return dataclasses.replace(sc, manual={c: share[c] + sc.manual_weight * (sc.manual or {}).get(c, 0.0) for c in cands}, manual_weight=1.0)


def record_counts(k, request, order):
    """D-642 A: 셈에 쓴 기록 — {scope, total, by_cause(원인마다 k), prior(원인마다 기본 건수)}. count_equipment = 기본 1건(사용자 R3 · 작은 표본 100% 막기)."""
    cands = [c.code for c in rules.ordered_causes(k, request["group"])]
    scope, n = graph_query.scope_basis(request["group"], request.get("equipment_id") if order == "count_equipment" else None, request.get("occurred_at"))
    by = {c: n.get(c, 0) for c in cands}
    return {"scope": scope, "total": sum(by.values()), "by_cause": by, "prior": PRIOR if order == "count_equipment" else 0}


def count_scoring(k, request, order, sc):
    """정본 확률(D-543): 원인 확률 = 범위 안 관계 있는 이력의 원인별 개수(+ 기본 건수) 비율(기본 점수 · 비슷한 10건 몫 0).
    매뉴얼 결과(④)를 켰으면 그 몫을 더함. 사본 session · rules 손 0 — 비율은 사본의 매뉴얼 몫 자리(무게 1)로."""
    rc = record_counts(k, request, order)
    n = {c: v + rc["prior"] for c, v in rc["by_cause"].items()}
    tot = sum(n.values())
    share = {c: v / tot if tot else 0.0 for c, v in n.items()}
    return dataclasses.replace(sc, alpha=NO_ZERO, manual={c: share[c] + sc.manual_weight * (sc.manual or {}).get(c, 0.0) for c in n}, manual_weight=1.0)


def _decide(request, options=None, extra_events=(), ex=None):
    """다음 단계 하나 + D-560 덧층: 후보(reason_facts.alternatives)마다 그래프 점검 절 설명(desc) — 사본 session은 손 0."""
    return graph_query.with_desc(_plain(request, options, extra_events, ex))


def _plain(request, options=None, extra_events=(), ex=None):
    k = manual()
    similar = [rules.Similar(s["record_id"], s.get("cause"), s["score"]) for s in request.get("similar", [])]
    every = [*request.get("events", []), *extra_events]
    events = exception.without(every)   # 예외 절 사건은 사본에 넘기지 않음
    if ex is None and request.get("exception_relation"):
        ex = graph_query.exception_relation(request["exception_relation"])
    if ex is not None:
        base = exception.base_events(every, ex)   # D-566 덧 2: 예외 안내 뒤 첫 재가동 · 재가동 값은 예외 몫(원래 순서 판정에서 뺌)
        return exception.step(_decide({**request, "events": base, "exception_relation": None}, options), ex, every, request.get("grades", {}), graph_query.release()["layer"], request.get("minutes", {}))
    threshold = (options or {}).get("call_threshold", rules.CALL_THRESHOLD)
    sc = scoring(options, request.get("manual_support"))
    order = (options or {}).get("order_signal", "similar")
    if order not in ORDER_SIGNALS:
        raise ValueError(f"모르는 순서 신호: {order}")
    if order == "similar":
        state = session.fold(k, request["group"], similar, events)
        d = session.decide(state, k, request.get("grades", {}), request.get("minutes", {}), threshold, sc)
        return order_overlay.apply(d, state, k, request.get("grades", {}), request.get("minutes", {}), options)   # D-565 check_before 덧층(기본 [] = 그대로)
    state = session.fold(k, request["group"], [], events)   # 몫은 evidence_scoring · count_scoring으로만 — 근거 n · 근거 적음은 비슷한 10건 그대로(아래)
    sc2 = evidence_scoring(k, request["group"], similar, sc) if order == "evidence" else count_scoring(k, request, order, sc)
    d = order_overlay.apply(session.decide(state, k, request.get("grades", {}), request.get("minutes", {}), threshold, sc2), state, k,
                            request.get("grades", {}), request.get("minutes", {}), options)   # D-565 check_before 덧층(기본 [] = 그대로)
    keep = {"evidence": rules.evidence_counts(state.candidates, similar), "low_evidence": rules.low_evidence(similar)}
    out = {**d, **{key: v for key, v in keep.items() if key in d}}
    counted = order in ("count_group", "count_equipment") and "probabilities" in d   # similar는 위에서 끝남 · evidence는 칸 0(약속 README)
    return {**out, "record_counts": record_counts(k, request, order)} if counted else out   # D-642 앱 '이 설비 기록 n건 중 k건'


def manual_support(group, manual_hits):
    """④ 매뉴얼 상위 절 → 원인 몫: 원인 c = Σ score(그 원인의 점검 절 · 조치 절인 조각). 조치 절을 함께 쓰는 원인(예 공구 교체)은
    모두 받음 · 공통 · 안내 · 사전 점검 절은 원인 없음."""
    k = manual()
    owner = {}
    for sec, c in [(c.section_id, c.cause) for c in k.checks.get(group, [])] + [(c.action_section, c.code) for c in k.causes.get(group, [])]:
        owner.setdefault(sec, []).append(c)
    out = {}
    for h in manual_hits or []:
        for c in dict.fromkeys(owner.get((h.get("locator") or {}).get("section"), [])):
            out[c] = round(out.get(c, 0.0) + h["score"], 6)
    return out


def _cue(request, with_boundary):
    """① 센서 단서 = 알람 문장 꼴 '이름 값 단위[, 경계 경계값 단위]' — 이름 · 단위 · 경계 = custom/alarm_rules.json(알람 node와 같은 표)."""
    q = request["query"]
    s = {**q["sensor_snapshot"], **alarm.derived(q["sensor_snapshot"])}
    rule = next(r for r in alarm.table()["rules"] if r["group"] == request["group"])
    part = f"{rule['sensor']} {s[alarm_rules.SNAPSHOT_KEY[rule['key']]]} {rule['unit']}"
    b = rule["boundaries"].get("low" if q.get("direction") == "경계 아래" else "high")
    return f"{part}, 경계 {b} {rule['unit']}" if with_boundary and b is not None else part


def history_query(request, options=None):
    """이력 리트리버 질의(약속 v4 start, Pack EV-001 PC-04): 글 = 증상군 이름 · 알람 방향(앱 R3 query_text, 세션 시작이라 점검 결과 없음) ·
    필터 = 증상군 · 재정렬 = 센서 7칸(5칸 + alarm과 같은 식의 계산 칸 둘). 요청 similar(다리 칸)는 쓰지 않는다(H-5 (나))."""
    q = request["query"]
    sensors = {**q["sensor_snapshot"], **alarm.derived(q["sensor_snapshot"])}
    text = " ".join(p for p in (GROUP_NAME.get(request["group"], ""), q.get("direction", "")) if p)
    form = (options or {}).get("query_text", "group_direction")
    if form != "group_direction":
        text = f"{text} {_cue(request, form == 'sensor_boundary')}"
    ctx = {k: request[k] for k in ("equipment_id", "occurred_at", "line_id") if k in request}   # 약속 v7 선택 칸 — 있을 때만(없으면 v6과 같은 질의)
    return {"history_query": {"text": text, "group": request["group"], "sensors": sensors, **ctx}}


def start(request, history_hits, manual_hits=None, options=None):
    """첫 Run(약속 v4 kind start): retrieve_history 상위 10건 → similar · 그 10건으로 첫 판정 · retrieval 넷(조각 값만, H-6 (가)).
    record_id = 조각 locator.record_id · cause = 조각 metadata.cause(return_metadata — 고른 뒤에만, D-305) · 순서 = packrt (-score, 조각 id)."""
    similar = [{"record_id": h["locator"]["record_id"], "cause": (h.get("metadata") or {}).get("cause"), "score": h["score"]} for h in history_hits]
    retrieval = {"source": "packrt", "vector_db_version": None, "query_from": None, "fallback_used": None}
    if history_hits:
        retrieval.update(vector_db_version=history_hits[0]["index_release"],
                         query_from="embed" if any((h.get("ranks") or {}).get("vector") is not None for h in history_hits) else None,
                         fallback_used=bool(history_hits[0].get("fallback_used")))
    req = {**request, "similar": similar}
    result = {"decision": None, "similar": similar, "retrieval": retrieval}
    if (options or {}).get("use_manual"):   # ④ 켤 때만 — 앱이 저장해 다음 판정 요청에 다시 보냄(약속 v5 덧칸)
        req["manual_support"] = result["manual_support"] = manual_support(request["group"], manual_hits)
    q = request.get("query") or {}
    ex = graph_query.exception_match(request["group"], {**q["sensor_snapshot"], **alarm.derived(q["sensor_snapshot"])}, request.get("equipment_id"), occurred_at=request.get("occurred_at"),
                                     recent_work=request.get("recent_work")) if q.get("sensor_snapshot") else None
    if ex is not None:   # 약속 v8 덧칸 — 앱이 relation_id를 저장해 judge 요청 exception_relation으로 다시 보냄
        plain = _decide(req, options, ({"type": "lock"}, {"type": "precheck_done"}))   # 예외 절이 없었을 때의 다음 점검(앱 카드 '원래 순서 다음')
        why = exception.matched(graph_query.release(), ex[0], request.get("equipment_id"), request.get("occurred_at"), request.get("recent_work"))
        result["exception_guide"] = exception.guide(ex, request.get("grades", {}), request.get("minutes", {}), plain.get("section"), why)
    result["decision"] = _decide(req, options, ex=ex)
    result["preview"] = _decide(req, options, ({"type": "lock"}, {"type": "precheck_done"}), ex=ex)   # 앱 진단 화면 '다음 점검' 미리보기(약속 v5 덧칸)
    return {"result": result}


def judge(request, options=None):
    """다음 Run(약속 v2 kind judge): 저장한 상위 10건을 다시 담은 요청으로 다음 단계 하나. session_id는 trace 입력에만 남고 판정에 쓰지 않는다."""
    return {"result": {"decision": switch.apply(_decide(request, options), manual(), request)}}   # D-556 약속 v9: 점검 바꾸기 · 예외 진행 덧층
