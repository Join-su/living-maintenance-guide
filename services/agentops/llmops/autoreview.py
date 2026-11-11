"""D-462 B (D-461 (5) · D-462 덧 2): the efficiency wheel's quality review — after the table's first row passed its three Runs and
before it becomes a version (⑥ 앞).

For every work case whose output differs, the changed node's output in the 운영 baseline and in the row are each asked the three
questions at once by Jev (decide@1, noul): one output per call with the node's input, nothing that says which version or model it is.
The code compares the two answers: the row worse than the base on a question — ① not polished where the base is, ② another field
mixed in or ③ the meaning changed where the base did not — is a finding; the reason (question · field · question text) is made here,
not by the model. A finding the two answers do not both hold at confidence 0.7 or more asks a person instead of dropping the row
(D-462 덧 2). The decision itself (drop · person · pass) is `verdict`."""
import hashlib
import json
import re

MIN_CONFIDENCE = 0.7
DROP = "품질 검토 탈락"
HUMAN = "끝 · 알림 — 품질 검토 확신도 0.7 미만 · 줄 {row} · 사람 확인 필요"
FAILED = "멈춤 · 품질 검토 못 함 — {code}"
HUMAN_SAID = "줄 {row} 품질 검토 사람 확인 — {word}({by}) · {reason}"   # D-652 다섯째: a person passes or drops the held row
HUMAN_DROP = DROP + " — 사람 확인 버림({by}) · {reason}"
SKIP_FAKE = "건너뜀 — 가짜 모드(검토 답 없음)"
SKIP_OFF = "건너뜀 — 품질 검토 꺼짐(LLMOPS_AUTO_REVIEW=off)"
SOURCE = "에이전트"
# Jev's price per 1M tokens (input, output): Zuplo · LiteLLM pages, TypeSafe's own price page not confirmed (packrt report 141)
JEV_USD_PER_1M = (0.042, 0.0)
UNIT_REVIEW = 0.001   # one decide call, careful (about 1k input tokens are $0.00004)

# the same three questions for every Pack: the node's input is its own draft, its output the fields it writes (D-460 wording, made general)
QUESTIONS = {
    "q1": {"type": "noul", "instructions": "출력이 입력과 다르게 다듬어졌나?",
           "criteria": {"true": "출력의 칸마다 입력 글을 그대로 옮기지 않고 다듬어 씀",
                        "false": "한 칸이라도 입력과 글자까지 같거나, 다듬지 않고 다른 칸 글을 붙이기만 함"}},
    "q2": {"type": "noul", "instructions": "출력의 한 칸에 다른 칸의 글이 섞였나?",
           "criteria": {"true": "한 칸에 그 칸의 일이 아닌 다른 칸의 일(미해결 일 · 할 일 등)이 들어감",
                        "false": "칸마다 제 일만 있음 — 입력의 관찰 · 근거 기록을 그 칸의 근거로 쓴 것은 섞임이 아님"}},
    "q3": {"type": "noul", "instructions": "출력에서 입력의 뜻이 바뀌었나?",
           "criteria": {"true": "출력의 뜻이 입력과 달라짐(없던 사실을 새로 만듦 · 다른 일이 그 칸의 일처럼 읽힘)",
                        "false": "같은 뜻을 다른 말로 썼거나 입력 글 그대로임"}},
}
LABEL = {"q1": "① 다듬지 않음", "q2": "② 다른 칸 섞임", "q3": "③ 뜻 바뀜"}
WORSE = {"q1": False, "q2": True, "q3": True}   # the row's answer that is worse than the base's opposite one


class ReviewError(ValueError):
    code = "review_answer"


def out_sha(output):
    return hashlib.sha256(json.dumps(output, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _visit(case, node):
    return next((v for v in (case or {}).get("visits") or [] if v.get("node") == node), None)


def _final(case):
    return next((v.get("output") for v in reversed((case or {}).get("visits") or []) if v.get("output") is not None), None)


def items(base_run, row_run, node, case_ids, inputs=None):
    """The work cases to ask about: {case, input, base, row} — the changed node's input and its two outputs when both Runs visit it,
    else the two final outputs with the case input (a structure row). A case whose two outputs are the same is left out."""
    out = []
    bcs, rcs = (base_run or {}).get("cases") or [], (row_run or {}).get("cases") or []
    for i, cid in enumerate(case_ids):
        bc, rc = (bcs[i] if i < len(bcs) else None), (rcs[i] if i < len(rcs) else None)
        bv, rv = _visit(bc, node), _visit(rc, node)
        if bv and rv:
            it = {"case": cid, "input": bv.get("input"), "base": bv.get("output"), "row": rv.get("output")}
        else:
            it = {"case": cid, "input": (inputs or [None] * len(case_ids))[i], "base": _final(bc), "row": _final(rc)}
        if out_sha(it["base"]) != out_sha(it["row"]):
            out.append(it)
    return out


def read(probs):
    """{question: noul probability} → {question: {value, confidence}} (p ≥ 0.5 is yes; confidence max(p, 1 − p), as packrt's decide@1)."""
    out = {}
    for q in QUESTIONS:
        p = (probs or {}).get(q)
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            raise ReviewError(f"질문 {q}의 noul 확률이 0 ~ 1 숫자가 아님")
        out[q] = {"value": p >= 0.5, "confidence": round(max(p, 1 - p), 6)}
    return out


def ask(decide, model, inp, output):
    """One output asked the three questions at once: decide(model, state, questions) → (raw, usage)."""
    raw, usage = decide(model, {"입력": inp, "출력": output}, QUESTIONS)
    answers = (raw or {}).get("answers") if isinstance(raw, dict) else None
    if not isinstance(answers, dict):
        raise ReviewError("decision 응답에 answers가 없음")
    return read({q: (a or {}).get("noul") for q, a in answers.items() if isinstance(a, dict)}), usage or {}


def _norm(v):
    return re.sub(r"\s+", " ", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)).strip()


def _leaves(v, out=None):
    """{leaf name: text} of the text fields inside a value (the last key names it: report_text.cause → cause); lists are left out."""
    out = {} if out is None else out
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, dict):
                _leaves(x, out)
            elif isinstance(x, str):
                out.setdefault(k, x)
    return out


def _fields(question, inp, out):
    """Which fields of the row's output the finding is about (read from the texts, not from the model): the output's text fields
    by name against the input's text fields of the same name, at any depth of the node's own shape."""
    o, i = _leaves(out), _leaves(inp)
    if not o:
        return "출력"
    if question == "q1":   # a field as the input wrote it (or the input's text with something put after it)
        hit = [k for k in o if k in i and _norm(o[k]).startswith(_norm(i[k]))]
    elif question == "q2":   # a field holding a piece of another input field's text that its own input field did not have
        hit = [k for k in o if any(len(s) >= 6 and s in _norm(o[k]) and s not in _norm(i.get(k, ""))
                                   for j in i if j != k for s in re.split(r"\s*/\s*|\s*→\s*", _norm(i[j])))]
    else:
        hit = [k for k in o if _norm(o[k]) != _norm(i.get(k))]
    return " · ".join(hit) or "출력 전체"


def findings(case, inp, base_out, row_out, base_ans, row_ans):
    out = []
    for q, bad in WORSE.items():
        if row_ans[q]["value"] is bad and base_ans[q]["value"] is not bad:
            yes = lambda v: "예" if v else "아니오"
            out.append({"case": case, "question": q, "field": _fields(q, inp, row_out), "source": SOURCE,
                        "sure": min(row_ans[q]["confidence"], base_ans[q]["confidence"]) >= MIN_CONFIDENCE,
                        "confidence": min(row_ans[q]["confidence"], base_ans[q]["confidence"]),
                        "why": f"{QUESTIONS[q]['instructions']} 후보 {yes(row_ans[q]['value'])} · 운영 {yes(base_ans[q]['value'])}"})
    return out


def verdict(fs):
    """drop: a finding both answers hold at 0.7 or more · human: findings, none that sure · pass: none."""
    if any(f["sure"] for f in fs):
        return "drop"
    return "human" if fs else "pass"


def reason(fs):
    """The row's one-line reason: '품질 검토 탈락 — ① 다듬지 않음 3건(cause · action) · ② 다른 칸 섞임 1건(cause)'."""
    parts = []
    for q in QUESTIONS:
        mine = [f for f in fs if f["question"] == q and f["sure"]]
        if mine:
            fields = []
            for f in mine:
                for x in f["field"].split(" · "):
                    if x not in fields:
                        fields.append(x)
            parts.append(f"{LABEL[q]} {len(mine)}건({' · '.join(fields)})")
    return f"{DROP} — " + " · ".join(parts)


def usd(usage):
    u = usage or {}
    return (u.get("input_tokens") or 0) * JEV_USD_PER_1M[0] / 1e6 + (u.get("output_tokens") or 0) * JEV_USD_PER_1M[1] / 1e6
