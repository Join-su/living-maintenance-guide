"""Scoring, diagnosis and recommendation (P-07), as pure functions over a finished Run record. The rules are the approved
Prototype Bundle's rules, copied from the plan; the result is computed once when the Run ends and stored with the Run, so
reopening a Run shows the same scores, diagnoses and recommendation."""
import re

STOPPED = "검토 전 멈춤(2차 범위)"   # EV-002 CHG-11: counts as a finished case (success); final output = the review subject
DONE = ("완료", STOPPED)
SUITE = "평가 suite"
SUITE_NOTE = "값 없음 — 평가 suite Run은 scorecard 참고"   # EV-002 CHG-12: Project scoring is not applied to suite Runs
EMPTY_SEARCH_RE = re.compile(r"(?i)(검색 결과(가)? 없|빈 검색|empty (search|result)|no (documents|results) found)")


def rule_score(project, run, case):
    """Data Runs: expected contained and forbidden not contained in the final output. Input-set Runs: no validation data."""
    if run["input"]["kind"] != "검증데이터":
        return "검증데이터 없음"
    if case["state"] not in DONE:
        return None
    final = case.get("final") or ""
    expected, forbidden = case.get("expected") or "", case.get("forbidden") or ""
    return {"match": bool(expected) and expected in final, "forbidden": bool(forbidden) and forbidden in final}


def diagnose(case, limit):
    st, err = case["state"], case.get("error") or {}
    if st == "timeout":
        return {"type": "timeout", "rule": "case 실행 시간이 제한을 넘음 → timeout",
                "evidence": f"node {err.get('node', '—')} · {(case.get('ms') or limit * 1000) / 1000:.1f}s · 제한 {limit}s"}
    if st == "실패":
        summary = err.get("summary", "")
        if err.get("key_missing"):
            return {"type": "키 없음", "rule": "오류가 키를 찾지 못한 것 → 키 없음", "evidence": f"node {err.get('node', '—')} · {summary}"}
        if EMPTY_SEARCH_RE.search(summary):
            return {"type": "빈 검색", "rule": "오류가 검색 결과 없음 → 빈 검색", "evidence": f"node {err.get('node', '—')} · {summary}"}
        return {"type": "규칙에 맞는 유형 없음", "rule": "node 예외 → 고정 규칙 목록(timeout·키 없음·빈 검색)에 맞는 유형 없음",
                "evidence": f"node {err.get('node', '—')} · {summary}"}
    if st == "실행 안 됨":
        return {"type": "실행 안 됨", "rule": "취소로 실행되지 않음", "evidence": "—"}
    if st == STOPPED:
        return {"type": "실패 없음(검토 전 멈춤)", "rule": "실패·timeout 없음", "evidence": "—"}
    return {"type": "실패 없음", "rule": "실패·timeout 없음", "evidence": "—"}


def summarize(project, run):
    sc, cases = project["scoring"], run["cases"]
    done = [c for c in cases if c["state"] in DONE]
    fails = sum(c["state"] == "실패" for c in cases)
    tos = sum(c["state"] == "timeout" for c in cases)
    m = {"total": len(cases), "ok": len(done), "fails": fails, "tos": tos}
    rule_cases = [c for c in done if isinstance(c.get("rule"), dict)]
    if sc["skip"]:
        m["rule"] = {"none": "채점 생략"}
    elif not sc["rule"]:
        m["rule"] = {"none": "규칙 채점 꺼짐"}
    elif run["input"]["kind"] != "검증데이터":
        m["rule"] = {"none": "검증데이터 없음"}
    elif rule_cases:
        m["rule"] = {"v": sum(c["rule"]["match"] and not c["rule"]["forbidden"] for c in rule_cases) / len(rule_cases)}
    else:
        m["rule"] = {"none": "완료된 case 없음"}
    llm_cases = [c for c in done if isinstance(c.get("llm"), dict)]
    if sc["skip"]:
        m["llm"] = {"none": "채점 생략"}
    elif not sc["llm"]:
        m["llm"] = {"none": "LLM 채점 꺼짐"}
    elif any(c.get("llm") == "채점 안 됨(키 없음)" for c in done) and not llm_cases:
        m["llm"] = {"none": "LLM 채점 안 됨(키 없음)"}
    elif llm_cases:
        m["llm"] = {"v": sum(c["llm"]["score"] for c in llm_cases) / len(llm_cases)}
    else:
        m["llm"] = {"none": "완료된 case 없음"}
    parts = [x["v"] for x in (m["rule"], m["llm"]) if "v" in x]
    if run["status"] == "취소됨":
        m["agg"] = {"none": "취소된 Run"}
    elif parts:
        m["agg"] = {"v": sum(parts) / len(parts)}
    else:
        m["agg"] = {"none": "채점 생략" if sc["skip"] else "점수 없음"}
    if run["input"]["kind"] == SUITE:
        m["rule"] = m["llm"] = m["agg"] = {"none": SUITE_NOTE}
    m["success"] = {"v": len(done) / len(cases) if cases else 0}
    m["latency"] = {"v": sum(c.get("ms") or 0 for c in done) / len(done) / 1000} if done else {"none": "완료된 case 없음"}
    ran = [c for c in cases if c["state"] != "실행 안 됨"]
    m["tokens"] = {"none": "token 미측정(실제 모드)"} if any(c.get("tokens") is None for c in ran) else {"v": sum(c.get("tokens") or 0 for c in ran) / 1000}
    m["rec"] = recommend(project, run, m)
    return m


def scorable(project, run):
    """Whether this Run can have a score at all: scoring not skipped and at least one scorer applies to it (rule scoring needs
    validation data; LLM scoring needs the LLMOps key). A Run that cannot have a score gets no recommendation; a scorable
    Run whose cases all failed has no score to compare but is recommended reject ('모두 실패 → reject')."""
    sc = project["scoring"]
    if sc["skip"]:
        return False
    return bool((sc["rule"] and run["input"]["kind"] == "검증데이터") or (sc["llm"] and run.get("llmKey", True)))


def recommend(project, run, m):
    rule = project["rule"]
    if run["status"] == "취소됨":
        return {"none": "취소된 Run — 권고 없음", "rule": "취소됨 → 권고 없음"}
    if run["input"]["kind"] == SUITE:   # EV-002 CHG-14: a person decides from the scorecard; a model judge alone never adopts
        return {"none": "권고 없음 — scorecard를 보고 판정", "rule": "평가 suite Run → 권고 없음"}
    if not scorable(project, run):
        return {"none": "권고 없음", "rule": "점수 없음 → 권고 없음"}
    if m["ok"] == 0:
        return {"v": "reject", "rule": "모두 실패 → reject"}
    if "v" not in m["agg"]:
        return {"none": "권고 없음", "rule": "점수 없음 → 권고 없음"}
    if m["fails"] + m["tos"] > 0:
        return {"v": "hold", "rule": f"실패 {m['fails']} · timeout {m['tos']} 있음 → hold"}
    agg = m["agg"]["v"]
    if agg >= rule["pass"]:
        return {"v": "adopt", "rule": f"실패 없이 집계 {agg:.2f} ≥ 기준 {rule['pass']:.2f} → adopt"}
    return {"v": rule["below"], "rule": f"실패 없이 집계 {agg:.2f} < 기준 {rule['pass']:.2f} → {rule['below']}"}


def evaluate(project, run, judge, suite=None, graph=None, node_judge=None, sections=None):
    """Fill rule/llm/diag per case and the Run summary. judge(case) returns {"score","why","provider"} or raises NoKey.
    Suite Runs (EV-002): no Project scoring; the summary carries the scorecard instead."""
    from .gateway import NoKey
    sc = project["scoring"]
    if run["input"]["kind"] == SUITE:
        for c in run["cases"]:
            c["rule"] = c["llm"] = SUITE_NOTE
            c["diag"] = diagnose(c, run["limit"])
        m = summarize(project, run)
        if suite is not None:
            from . import suites

            def nj(text, i, o):
                try:
                    return node_judge(text, i, o)
                except NoKey:
                    return "채점 안 됨(키 없음)"
                except Exception:  # noqa: BLE001 - a failed judge call must not leave the Run unfinished
                    return "채점 안 됨(호출 실패)"
            m["scorecard"] = suites.scorecard(run, suite["cases"], graph, nj if (suite.get("judge") and node_judge) else None,
                                              judge_settings=bool(suite.get("judge_settings")), sections=sections)
        return m
    for c in run["cases"]:
        c["rule"] = None if sc["skip"] or not sc["rule"] else rule_score(project, run, c)
        c["llm"] = None
        if not sc["skip"] and sc["llm"] and c["state"] in DONE:
            try:
                c["llm"] = judge(c)
            except NoKey:
                c["llm"] = "채점 안 됨(키 없음)"
            except Exception:  # noqa: BLE001 - a failed judge call must not leave the Run unfinished (L0)
                c["llm"] = "채점 안 됨(호출 실패)"
        c["diag"] = diagnose(c, run["limit"])
    return summarize(project, run)
