"""D-549 덧 3 B (packrt-1): the fix candidates of a small option space, every one tried — the 10-30 offline calculator's way (packrt-1
070: on the same problem trying every combination fixed more, cost less and broke less than the agent and the same number of random
tries).

The space is the responsible node's options that can make a change line (autohyp.fill_knobs: makeable · within the Pack's limits), no
vector-DB rebuild option (a build a combination), each over its values — a choice · filter key every value, a number a coarse grid on
the Pack's steps (both ends · the present value · evenly between) — at most MAX_CHANGED options changed at once. Each combination is one
'option bundle' hypothesis (autohyp HYP_KINDS 'bundle'). Small = combinations × the cases through the node within the case-run cap
(LLMOPS_AUTO_GRID_MAX): ⑤ runs each bundle's cheap test (no judge), counts fixed · broken the table's way, and only the first by the
10-30 order goes on to the table and ⑥ — the checks after it stay as they are. A larger space goes the agent's (real) or the rules'
(fake) way — D-549 덧 13: unless no model is on the cases' path, then the Pack's own process runs every combination fast (screen_child) and
only the first few by the same order go on as bundles; an agent hypothesis there names its basis by address (D-549 덧 12: case › path in that case's record as it was sent,
basis_missing reads the value there itself) — one that names none, or a wrong one, is marked '근거 못 댐' and tried after those that do."""
import itertools
import json
import re

from . import autodeviate, autohyp

MAX_CHANGED = 3   # options changed at once (10-30: the rows changing fewer options broke no hidden case)
GRID_POINTS = 5   # a number option's points: both ends, the present value, evenly between, on the Pack's step
FLOAT_STEP = 0.1  # a float option without a step: its grid's step
CASE_RUNS = 400   # combinations × cases a small space may cost (≈ 20 minutes at ~3 s a case, A-27); LLMOPS_AUTO_GRID_MAX
PARALLEL = 4      # cheap tests of one space running at once

START = "전부 시험 {n} 조합 · case {m} — {node}의 옵션 조합(바꾸는 옵션 3개까지)마다 싼 시험"
NOT_SMALL = "전부 시험 안 함 — 조합 {n} × case {m} = {x} > 상한 {cap}"
OVER_CAP = "전부 시험 안 함 — 싼 시험 {n}번 비용이 상한을 넘음"
NONE_FIXES = "전부 시험 {n} 조합 — 고치는 조합 없음(망가짐 0 · 고침 > 0 없음)"
FIRST = "전부 시험 {n} 조합 · 1등 {combo} — 고침 {f} · 망가짐 {b} → 후보 표"
NOT_FIRST = "버림 — 전부 시험 1등 아님(고침 {f} · 망가짐 {b})"
SCREEN_RUNS = 60000   # combinations × cases a pre-screen may run (scene ③: 32,664)
SCREEN_TOP = 10       # combinations that go on to the cheap test
SCREEN_TIMEOUT = 1500   # seconds a pre-screen may take (it holds the wheel's pass)
SCREEN_START = "미리 거르기 — 조합 {n} × case {m}을 Pack 안에서 바로 셈(모델 부르는 node 없음 · 질의 임베딩만)"
SCREEN_BIG = "미리 거르기 안 함 — 조합 {n} × case {m} = {x} > 상한 {cap}"
SCREEN_MODEL = "미리 거르기 안 함 — case 길에 모델 부르는 node {node}"
SCREEN_OPS = "미리 거르기 안 함 — 바꿈 줄 꼴 {op}"
SCREEN_CUT = "미리 거르기 — 기록을 줄이는 조합 {n}개는 뒤로"
SCREEN_FILE = "screen-rank.jsonl"   # under the wheel's folder: every combination in order, its counts only
SCREEN_SAVED = "미리 거르기 — 순위 전체 {n}줄을 파일로 남김"
SCREEN_FAILED = "미리 거르기 못 함 — {code} · {where}"
SCREEN_NONE = "미리 거르기 끝 — 고치는 조합 없음(조합 {n})"
SCREEN_END = "미리 거르기 끝 — 고치는 조합 {k}개 중 앞 {t}개 싼 시험 · 1등 {combo} · 고침 {f} · 망가짐 {b} · 기록 줄인 case {c} · 현장 {d}"
SCREEN_WHY = "미리 거르기 {i} / {t} — 고침 {f} · 망가짐 {b} · 기록 줄인 case {c} · 현장 {d}"
NO_BASIS = "근거 못 댐({what})"   # D-549 덧 12: kept, tried after the hypotheses whose basis was found
NO_BASIS_LINE = "근거 못 댐 {n}개 — 근거 댄 가설 뒤에 시험: {items}"
BAD_ADDRESS = " · 적은 주소 앞 80자: {text}"
ADDRESS_SHOWN = 80
ANSWER_KEYS = ("expected", "field_truth")   # agent_cases' answer cells: no record of the fault, never shown in a line
NOT_RECORD = ANSWER_KEYS + ("case", "set")   # an address's first step names a record cell, not the case's id or set
DROP_PAIR = "버림 — 묶음 안 두 옵션이 맞지 않음(후보 수 ≥ top_k · 오버랩 < 청크)"


def knobs(node, limits=None):
    """The options the space steps: what a rule fill may step (fill_knobs — makeable, within the Pack's limits), no rebuild option."""
    return {k: v for k, v in autohyp.fill_knobs(node, False, limits).items() if not k.startswith("index.") and not autohyp.is_order_knob(k)}   # D-565: its own kind


def _on_step(v, lo, st):
    return round(lo + round((v - lo) / st) * st, 10)


def values(node, knob, spec, limits=None):
    """The values an option takes in the space (the present one first), or None when it cannot be gridded."""
    cur = autohyp.present(node, knob, spec)
    t = spec["type"]
    if t == "choice":
        vals = list(spec["values"])
    elif t == "filter_key":
        vals = [None, *(autohyp.current(node, "filter") or [])]   # None = the filter as it is; a key = that key taken out
        cur = None
    elif t in ("int", "float"):
        lo, hi = spec["min"], spec["max"]
        if knob == "top_k" and (limits or {}).get("top_k"):
            hi = min(hi, limits["top_k"][0])
        st = spec.get("step") or (1 if t == "int" else FLOAT_STEP)
        n = int(round((hi - lo) / st))
        if n + 1 <= GRID_POINTS or (t == "float" and not spec.get("step")):
            pts = [lo + i * st for i in range(n + 1)]
        else:
            pts = [_on_step(lo + (hi - lo) * i / (GRID_POINTS - 1), lo, st) for i in range(GRID_POINTS)]
        pts = [int(round(p)) if t == "int" else round(p, 10) for p in pts]
        vals = sorted(set(pts) | ({cur} if isinstance(cur, (int, float)) and not isinstance(cur, bool) else set()))
    else:
        return None   # model · prompt · list: not a grid option
    return [cur] + [v for v in vals if v != cur]


def space(node, knobs, limits=None, max_changed=MAX_CHANGED):
    """[{option: value}] of every combination that changes 1 ~ max_changed options, each value one the option check lets through on its
    own and the combination keeping packrt's pairs."""
    axes = []
    for k, spec in knobs.items():
        vs = values(node, k, spec, limits)
        if not vs:
            continue
        ok = [v for v in vs[1:] if not autohyp.option_check({"node": node["name"], "knob": k, "value": v, "predicts": ["*"]},
                                                            {node["name"]: node}, {"*"}, False, limits=limits)]
        if ok:
            axes.append((k, ok))
    out = []
    for r in range(1, max_changed + 1):
        for sub in itertools.combinations(axes, r):
            for vals in itertools.product(*(vs for _, vs in sub)):
                combo = dict(zip((k for k, _ in sub), vals))
                if pair_ok(node, combo):
                    out.append(combo)
    return out


def pair_ok(node, combo):
    """packrt's load rules between two options of one node: the rerank pool never below top_k, the overlap below the chunk size."""
    get = lambda k: combo[k] if k in combo else autohyp.current(node, k)   # noqa: E731
    top_k, pool = get("top_k"), get("rerank.pool")
    if isinstance(top_k, int) and isinstance(pool, int) and pool < top_k:
        return False
    size, over = get("index.chunk_size"), get("index.overlap") or 0
    return not (isinstance(size, int) and over >= size)


def knob_text(combo):
    return " · ".join(combo)


def combo_text(combo):
    return " · ".join(f"{k} {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}" for k, v in combo.items())


def small(n_combos, n_cases, cap=CASE_RUNS):
    """None when the space is small enough to try whole, else the log line why not."""
    x = n_combos * max(n_cases, 1)
    return None if 0 < x <= cap else NOT_SMALL.format(n=n_combos, m=n_cases, x=x, cap=cap)


def rank(r):
    """The 10-30 order (optgrid._rank): no break and a fix first → the field cases right (a table criterion, D-587) → fewer cases with records cut
    (cut_records) → net gain → fewer breaks → fewer options changed → the combination's text."""
    return (not (r["broken"] == 0 and r["fixed"] > 0), -r.get("field", 0), r.get("cut", 0), -(r["fixed"] - r["broken"]), r["broken"], len(r["combo"]),
            json.dumps(r["combo"], ensure_ascii=False, sort_keys=True))


def records_of(case, node):
    """How many results the node's last visit of a case brought (either step), from the visit's own record; None when no such visit says so."""
    found = [v["retrievals"] for v in autodeviate.case_visits(case) if v.get("node") == node and isinstance(v.get("retrievals"), dict)]
    return len(found[-1].get("results") or []) if found else None


def cut_records(ids, counts, base):
    """How many cases brought fewer records than in the base (base = {case id: its count}): a combination that fixes by emptying what the judgment
    reads (real run ea87124: ten records cut to 0 ~ 1, the causes alike, the shortest check chosen) comes after one that does not. D-587: the record
    count, not the Pack's 'evidence is thin' — V10 keeps all ten and is thin in 3 of 8 cases, its scores lower. Behind, not left out."""
    return sum(1 for cid, n in zip(ids, counts) if n is not None and base.get(cid) is not None and n < base[cid])


def screen_pick(rows, n=SCREEN_TOP):
    """The pre-screen's rows that fix something, first by the 10-30 order, at most n: (those n, how many fix something)."""
    fixing = sorted((r for r in rows if r["fixed"] > 0), key=rank)
    return fixing[:n], len(fixing)


def first(rows):
    """The first row by rank when it fixes and breaks nothing, else None."""
    best = min(rows, key=rank) if rows else None
    return best if best and best["broken"] == 0 and best["fixed"] > 0 else None


def _steps(path):
    """'output.hits[0].cause' · 'output.hits.0.cause' → ['output', 'hits', 0, 'cause']."""
    out = []
    for part in re.findall(r"[^.\[\]]+", str(path or "")):
        out.append(int(part) if part.isdigit() else part)
    return out


def resolve(cases, address):
    """(True, the value) when the address names a cell of a case's record as it was sent — {"case": its id, "path": a dotted path
    from one of its record cells (output · first_output · signal · fault …)}; (False, None) for an unknown case, a path into the
    answer cells or the case's id · set, or a path that is not there."""
    if not isinstance(address, dict):
        return False, None
    case = next((c for c in cases or [] if isinstance(c, dict) and c.get("case") == address.get("case")), None)
    steps = _steps(address.get("path"))
    if case is None or not steps or steps[0] in NOT_RECORD:
        return False, None
    v = case
    for st in steps:
        if isinstance(v, dict) and st in v:
            v = v[st]
        elif isinstance(v, list) and isinstance(st, int) and st < len(v):
            v = v[st]
        else:
            return False, None
    return True, v


def basis_missing(h, cases):
    """What an agent hypothesis lacks of its basis — an address in the cases it was sent that is there (each one; D-549 덧 12), an
    effect, predicts — or None."""
    ev, eff = h.get("evidence"), str(h.get("effect") or "").strip()
    found = isinstance(ev, list) and bool(ev) and all(resolve(cases, a)[0] for a in ev)
    miss = [w for w, ok in (("근거 주소", found), ("예상 효과", eff), ("바뀔 case", h.get("predicts"))) if not ok]
    return " · ".join(miss) or None


def answer_strings(cases):
    """Every value in the sent cases' answer cells (expected · field truth; a check's path is a cell name, no value) — an address shown
    in a line holds none of them."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k != "path":
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif x is not None and str(x).strip():
            out.append(str(x))
    for c in cases or []:
        for k in ANSWER_KEYS:
            walk((c or {}).get(k))
    return out


def bad_address(h, cases, answers=()):
    """The address a '근거 주소'-lacking hypothesis wrote, its first ADDRESS_SHOWN characters — None when it named none, its basis was
    found, or the text holds an answer cell's string (no answer value on the screen)."""
    ev = h.get("evidence")
    if ev in (None, "", []) or (isinstance(ev, list) and all(resolve(cases, a)[0] for a in ev)):
        return None
    text = ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False, default=str)
    if any(a in text for a in answers):
        return None
    return text[:ADDRESS_SHOWN]


def no_basis(h):
    """A '근거 못 댐' hypothesis's mark: the cells it lacks, and the address it wrote when kept."""
    return NO_BASIS.format(what=h["basis_missing"]) + (BAD_ADDRESS.format(text=h["basis_address"]) if h.get("basis_address") else "")
