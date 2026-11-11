"""예외 칸 AI 나눔 마무리 node(custom_code@3 · 약속 v10 kind exception_split · D-566) — 망 · 호출 · 부작용 0.
앞 node split_exception(llm@1)이 낸 예외 하나(ExceptionSplit: topic + checks + actions)를 v10 꼴로 거른다 — 짝 꼴 없음(D-566):
점검 · 조치 칸마다 앞뒤 빈칸 뺌 · what이 빈 칸은 뺌(방법 · 기준은 빈 글 허용) · 분은 글에 'N분'으로 적힌 1 이상 정수만(아니면 null) · 부품은 빈 이름 뺌 ·
방법 칸의 '언제' 말 뺌 · 기준이 없다고 적은 기준 칸은 빈 글(D-597 · 원문 text는 앱에 그대로) ·
**지어낸 글 뺌**(what마다 글자 두 개 묶음 중 원문에 있는 비율 < SRC_MIN이면 그 칸을 뺌 · 띄어쓰기 · 문장부호 무시) ·
**같은 점검 / 같은 조치 합침**(글이 같으면 앞 칸 하나 · 부품 합침 · 분은 앞 값 없을 때만 뒤 값) · 목록마다 앞에서 MAX개 · 적은 순서 그대로.
점검 0 또는 조치 0이면 exception null + 까닭(D-566 덧 1 Q3 · 한쪽 0 = 나누지 못함): 모델이 그쪽을 0개 냄 = not_split · 냈지만 모두 꼴 밖 · 원문 밖 = invalid_output.
주제 topic: '예외상황' 머리 떼기 · 15자 안(마지막 띄어쓰기) · 비면 첫 조치 what 앞 15자.
모델 답 자체가 ExceptionSplit 꼴 밖이면 앞 node에서 Run 실패 그대로. 꼴 정본 = orchestration agents/c1a15 handoff/d566/contract-v10.md 1 · 2절."""
import re

MAX = 10
SRC_MIN = 0.4   # D-556 덧 8 · eval 153 시작값(맞는 나눔 최저 0.43 · 지어낸 글 0) — 실제 AI 출력 첫 셈 뒤 다시 맞춤
_DROP = re.compile(r"[\s\W_]+")
TOPIC_MAX = 15   # D-560 덧(eval 166): 주제 15자 안
_HEAD = re.compile(r"^\s*예외\s*상황\s*[-–—:·]?\s*")   # 모델이 붙인 '예외상황-' 머리(이름은 앱 · 추출기가 붙임)
_WHEN = re.compile(r"(\d+\s*(일|주|주일|달|개월|년|시간)\s*(전|뒤|후)|\d+\s*분\s*전|(며칠|몇\s*(일|주|달|개월)|(한|두|세|네|다섯|여섯)\s*(주|달|시간)|하루|이틀|사흘|나흘|닷새|일주일|보름)\s*(전|뒤|후)"
                   r"|지난\s*(주|달|번|해)|이번\s*(주|달)|재작년|작년|올해|그저께|그제|어제|오늘|(오전|오후)(\s*\d+\s*시(\s*\d+\s*분)?)?"
                   r"|\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일)(\s*에)?")   # D-597 · D-607 '언제' 말(칸이 없음 · 원문에 남음) · '5분 뒤'는 방법일 수 있어 뺌 · '11/3'은 '3/4 바퀴'와 갈리지 않아 뺌
_WHEN_REST = re.compile(r"(점검|정비|확인|교체|작업|보수)?(함|한|했음)?")   # '언제'를 뺀 뒤 이것뿐이면 방법이 아님
_NO_CRITERIA = re.compile(r"기준\s*(이|은|도)?\s*(없|미기재|모름|불명)|^(없음|해당\s*없음|미기재|모름)$")   # 기준이 없다고 적은 글 = 기준 아님('누유 없음'은 기준)


def _topic(raw, act):
    """주제: '예외상황' 머리 떼기 → 비면 조치 what 앞 15자 → 15자 넘으면 15자 안 마지막 띄어쓰기에서 자름(없으면 15자)."""
    t = _HEAD.sub("", _s(raw)).strip() or act[:TOPIC_MAX].strip()
    if len(t) <= TOPIC_MAX:
        return t
    cut = t.rfind(" ", 0, TOPIC_MAX + 1)
    return (t[:cut] if cut > 0 else t[:TOPIC_MAX]).strip()


def _s(v):
    return v.strip() if isinstance(v, str) else ""


def _min(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else None


def _norm(text):
    return _DROP.sub("", text or "")


def _written(m, src):
    """분은 글에 'N분'으로 적힌 값만(D-597: 실제 나눔이 '3일 전'의 3을 분 칸에 넣음)."""
    return m if m is not None and re.search(rf"(?<!\d){m}분", src) else None


def _ratio(field, src):
    f = _norm(field)
    grams = [f[i:i + 2] for i in range(len(f) - 1)] or [f]
    return sum(1 for g in grams if g in src) / len(grams)


def _how(text):
    """본 방법(D-597): '언제' 말을 뺌 · 남은 것이 '점검' 같은 말뿐이면 빈 글 · '언제'가 없으면 글 그대로."""
    if not _WHEN.search(text):
        return text
    rest = " ".join(_WHEN.sub(" ", text).split()).strip(" ,·")
    return "" if _WHEN_REST.fullmatch(_norm(rest)) else rest


def _criteria(text):
    return "" if _NO_CRITERIA.search(text) else text


def _check(c, src):
    c = c if isinstance(c, dict) else {}
    what = _s(c.get("what"))
    return {"what": what, "how": _how(_s(c.get("how"))), "criteria": _criteria(_s(c.get("criteria"))), "minutes": _written(_min(c.get("minutes")), src)} if what else None


def _action(a, src):
    a = a if isinstance(a, dict) else {}
    what = _s(a.get("what"))
    return {"what": what, "parts": [p for p in map(_s, a.get("parts") or []) if p], "minutes": _written(_min(a.get("minutes")), src)} if what else None


def _merge(rows, key):
    out, at = [], {}
    for r in rows:
        k = key(r)
        if k not in at:
            at[k] = len(out)
            out.append(r)
            continue
        first = out[at[k]]
        if "parts" in r:
            first["parts"] += [p for p in r["parts"] if p not in first["parts"]]
        if first["minutes"] is None:
            first["minutes"] = r["minutes"]
    return out


def _keep(rows, make, src, key):
    return _merge([r for r in (make(x, src) for x in rows) if r and _ratio(r["what"], src) >= SRC_MIN], key)[:MAX]


_NUM = re.compile(r"\d+(?:\.\d+)?")
SAY_MAX = 120


def _plain(r):
    """칸으로 짠 지시 문장(근거 = 칸 값뿐 · 덧말 0) — 점검: 무엇 확인 · 방법 · 판정 기준 / 조치: 무엇 · 부품."""
    if "parts" in r:
        return " · ".join(x for x in (r["what"], f"부품: {', '.join(r['parts'])}" if r["parts"] else "") if x)
    return " · ".join(x for x in (f"{r['what']} 확인", r["how"], f"판정: {r['criteria']}" if r["criteria"] else "") if x)


def _say(text, r, raw):
    """D-642 C 지시 문장(줄마다 instruction): 모델 문장은 원문에 없는 수 · 언제 말 · '기준 없음'이 없고 길이가 맞을 때만 — 아니면 칸으로 짬."""
    t = _s(text)
    ok = t and len(t) <= SAY_MAX and not _WHEN.search(t) and not _NO_CRITERIA.search(t) and set(_NUM.findall(t)) <= set(_NUM.findall(raw))
    return t if ok else _plain(r)


def finish(request, split_text):
    e = split_text.get("exception") if isinstance(split_text.get("exception"), dict) else {}
    raw_c, raw_a = e.get("checks") or [], e.get("actions") or []
    src, raw = _norm(request.get("text")), request.get("text") or ""
    said = lambda make: lambda c, s: {**x, "instruction": _say(c.get("instruction"), x, raw)} if (x := make(c, s)) else None   # 문장은 그 줄을 따라감(걸러지면 같이 빠짐)
    checks = _keep(raw_c, said(_check), src, lambda c: tuple(_norm(c[k]) for k in ("what", "how", "criteria")))
    actions = _keep(raw_a, said(_action), src, lambda a: _norm(a["what"]))
    if checks and actions:
        exc = {"topic": _topic(e.get("topic"), actions[0]["what"]), "checks": checks, "actions": actions}
        return {"result": {"exception_split": {"exception": exc, "reason": None}}}
    reason = "not_split" if not raw_c or not raw_a else "invalid_output"
    return {"result": {"exception_split": {"exception": None, "reason": reason}}}
