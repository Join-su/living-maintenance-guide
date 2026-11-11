"""Value lookups `{state.a.b}` / `{state.list[0]}` (spec 5): no computation. A string that is exactly one lookup keeps the
value's type; lookups inside text become text (objects and lists as JSON)."""
import json
import re

REF = re.compile(r"\{(state|item)((?:\.[a-z_][a-z0-9_]*|\[[0-9]+\])+)\}")
STEP = re.compile(r"\.([a-z_][a-z0-9_]*)|\[([0-9]+)\]")


class TemplateError(KeyError):
    pass


def lookup(root, path):
    cur = root
    for name, idx in STEP.findall(path):
        try:
            cur = cur[int(idx)] if idx else cur[name]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def _text(v):
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def render(value, state, item=None):
    """Render strings (recursively through dicts and lists)."""
    roots = {"state": state, "item": item}
    if isinstance(value, str):
        m = REF.fullmatch(value)
        if m:
            return lookup(roots[m.group(1)], m.group(2))
        return REF.sub(lambda m: _text(lookup(roots[m.group(1)], m.group(2))), value)
    if isinstance(value, dict):
        return {k: render(v, state, item) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, state, item) for v in value]
    return value


def by_path(state, dotted):
    """`state.a.b` (router `by`) -> value."""
    parts = dotted.split(".")[1:]
    cur = state
    for p in parts:
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur
