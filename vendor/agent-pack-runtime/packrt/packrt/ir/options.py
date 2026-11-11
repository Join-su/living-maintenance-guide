"""Function node options (packrt 0.15.0, D-476 · packrt 151): a custom_code@3 node may declare `params.options` — per name a type
(choice · number · integer · switch), its limits, the current value and an optional label. The Pack declares them; the runtime
passes the current values to the function as one `options` dict; a screen or an agent reads the declaration (node_options) and
changes values only inside the limits (patch op set_option). The declaration (without values) is a fixed-flow item."""
import math
import re

NAME = re.compile(r"^[a-z][a-z0-9_]*$")
KEYS = {"choice": {"type", "choices", "value", "label", "loosen"}, "number": {"type", "min", "max", "step", "value", "label", "loosen"},
        "integer": {"type", "min", "max", "step", "value", "label", "loosen"}, "switch": {"type", "value", "label", "loosen"}}
LOOSEN = ("up", "down", "none")   # D-476 덧 4: which way makes the judgement looser — number · integer: a larger (up) or smaller
                                  # value; switch: up = false → true; choice: up = later in choices. none (or no key) = neither
MAX_OPTIONS = 16
CHOICES = (2, 12)
LABEL_MAX = 40


def _num(x, whole=False):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and (not whole or float(x).is_integer() and isinstance(x, int))


def value_problem(opt, value):
    """Why `value` is not a value of the declared option `opt` ('' when it is)."""
    t = opt.get("type")
    if t == "choice":
        return "" if value in (opt.get("choices") or []) else f"고를 수 있는 값({' · '.join(map(str, opt.get('choices') or []))})이 아닙니다: {value!r}"
    if t == "switch":
        return "" if isinstance(value, bool) else f"켜기/끄기(true · false)가 아닙니다: {value!r}"
    if t in ("number", "integer"):
        if not _num(value, t == "integer"):
            return f"{'정수' if t == 'integer' else '수'}가 아닙니다: {value!r}"
        lo, hi, step = opt.get("min"), opt.get("max"), opt.get("step")
        if not lo <= value <= hi:
            return f"한도({lo} ~ {hi}) 밖입니다: {value!r}"
        if step is not None:
            k = (value - lo) / step
            if abs(k - round(k)) > 1e-9:
                return f"{lo}에서 {step} 칸 위가 아닙니다: {value!r}"
        return ""
    return f"모르는 형입니다: {t!r}"


def problems(decl):
    """[(name or None, message)] for a node's `options` declaration ([] when it is good)."""
    if not isinstance(decl, dict) or not decl:
        return [(None, "options는 이름: 선언 mapping이어야 합니다(비우지 않음)")]
    out = []
    if len(decl) > MAX_OPTIONS:
        out.append((None, f"옵션은 node마다 {MAX_OPTIONS}개까지입니다(지금 {len(decl)}개)"))
    for name, opt in decl.items():
        if not (isinstance(name, str) and NAME.match(name)):
            out.append((name, f"옵션 이름 '{name}'는 ^[a-z][a-z0-9_]*$ 꼴이어야 합니다"))
            continue
        if not isinstance(opt, dict):
            out.append((name, f"옵션 '{name}'의 선언은 mapping이어야 합니다"))
            continue
        t = opt.get("type")
        if t not in KEYS:
            out.append((name, f"옵션 '{name}'의 type은 {' · '.join(KEYS)} 가운데 하나여야 합니다: {t!r}"))
            continue
        extra = sorted(set(opt) - KEYS[t])
        if extra:
            out.append((name, f"옵션 '{name}'({t})에 쓸 수 없는 칸입니다: {', '.join(map(str, extra))}"))
            continue
        if "value" not in opt:
            out.append((name, f"옵션 '{name}'에 지금 값(value)이 없습니다"))
            continue
        if opt.get("loosen", "none") not in LOOSEN:
            out.append((name, f"옵션 '{name}'의 loosen은 {' · '.join(LOOSEN)} 가운데 하나여야 합니다: {opt.get('loosen')!r}"))
            continue
        label = opt.get("label")
        if label is not None and not (isinstance(label, str) and 0 < len(label) <= LABEL_MAX):
            out.append((name, f"옵션 '{name}'의 label은 {LABEL_MAX}자까지 글이어야 합니다"))
            continue
        if t == "choice":
            ch = opt.get("choices")
            if not (isinstance(ch, list) and CHOICES[0] <= len(ch) <= CHOICES[1]):
                out.append((name, f"옵션 '{name}'의 choices는 {CHOICES[0]} ~ {CHOICES[1]}개여야 합니다"))
                continue
            if not all(isinstance(c, str) and NAME.match(c) for c in ch) or len(set(ch)) != len(ch):
                out.append((name, f"옵션 '{name}'의 choices는 겹치지 않는 ^[a-z][a-z0-9_]*$ 이름이어야 합니다"))
                continue
        elif t in ("number", "integer"):
            whole = t == "integer"
            lo, hi, step = opt.get("min"), opt.get("max"), opt.get("step")
            if not (_num(lo, whole) and _num(hi, whole) and lo < hi):
                out.append((name, f"옵션 '{name}'은 min < max({'정수' if whole else '수'})가 있어야 합니다"))
                continue
            if step is not None and not (_num(step, whole) and step > 0):
                out.append((name, f"옵션 '{name}'의 step은 0보다 큰 {'정수' if whole else '수'}여야 합니다"))
                continue
        why = value_problem(opt, opt["value"])
        if why:
            out.append((name, f"옵션 '{name}'의 지금 값 — {why}"))
    return out


def values(params):
    """The current values a node's function gets ({} when it declares none) — a fresh dict every call."""
    return {k: v["value"] for k, v in ((params or {}).get("options") or {}).items()}


def declaration(params):
    """The declaration without values (the fixed-flow item)."""
    return {k: {kk: vv for kk, vv in v.items() if kk != "value"} for k, v in ((params or {}).get("options") or {}).items()}


def node_options(source):
    """Every declared option of a Pack, in graph · node · declaration order:
    [{graph, node, fn, name, type, choices | min · max · step, value, label, loosen}]. source = a Pack folder or a loaded doc."""
    if isinstance(source, dict):
        doc = source
    else:
        from . import load_pack
        doc = load_pack(source).doc or {}
    out = []
    for gname, g in (doc.get("graphs") or {}).items():
        for n in (g or {}).get("nodes") or []:
            p = n.get("params") or {}
            if not str(n.get("kind", "")).startswith("custom_code") or not isinstance(p.get("options"), dict):
                continue
            for name, opt in p["options"].items():
                row = {"graph": gname, "node": n.get("id"), "fn": p.get("fn"), "name": name, "type": opt.get("type")}
                if opt.get("type") == "choice":
                    row["choices"] = list(opt.get("choices") or [])
                elif opt.get("type") in ("number", "integer"):
                    row.update(min=opt.get("min"), max=opt.get("max"), step=opt.get("step"))
                row.update(value=opt.get("value"), label=opt.get("label"), loosen=opt.get("loosen", "none"))
                out.append(row)
    return out
