"""Format-keeping rewrite of a strict-YAML file (EV-017): given the original text and the new value, change only the spans
whose value changed. Comments, key order, key quoting (`"none"`, `"true"`) and every unchanged line stay byte for byte.

    new_text = rewrite(text, new_value)

How: the original is composed into PyYAML nodes (with source positions) and compared with the new value, top down.
- equal value: the span is left alone
- mapping: changed values are edited in place, removed keys are cut out, added keys are inserted after the last key
- block sequence: items are matched (by `id` when the items are mappings with an id, else by content); matched items are
  edited in place, removed items are cut out, new items are inserted after the item before them
- anything else (a scalar, a flow sequence, a changed type): the value's span is replaced with newly written YAML
New text is written in flow style when short (`{a: 1, b: [x, y]}`), in block style otherwise. The result is read back with
the strict loader and must equal the new value, or WriteError is raised (nothing half-written is returned)."""
import json
import re

import yaml

from . import strict_yaml

PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/@]*$")
PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
QUOTED_WORDS = strict_yaml.RESERVED_KEYS | {"none", "null", "nan", "inf"}
FLOW_MAX = 100   # longer values are written in block style


class WriteError(Exception):
    pass


def _canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False, default=repr)   # a value YAML cannot hold still compares; writing it fails later


def _same(a, b):
    return _canon(a) == _canon(b)


def _reads_back(text, v):
    try:
        got = yaml.load("x: " + text, Loader=strict_yaml._Loader)
    except yaml.YAMLError:
        return False
    return isinstance(got, dict) and _same(got.get("x"), v)


def scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        out = json.dumps(v)
        if _reads_back(out, v):
            return out
        raise WriteError(f"이 수를 YAML로 쓸 수 없습니다: {v!r}")
    if isinstance(v, str):
        if PLAIN.match(v) and v.lower() not in QUOTED_WORDS and _reads_back(v, v):
            return v
        return json.dumps(v, ensure_ascii=False)
    raise WriteError(f"쓸 수 없는 값입니다: {type(v).__name__}")


def key_text(k):
    if not isinstance(k, str):
        raise WriteError(f"키는 글자여야 합니다: {k!r}")
    return k if PLAIN_KEY.match(k) and k.lower() not in QUOTED_WORDS else json.dumps(k, ensure_ascii=False)


def flow(v):
    if isinstance(v, dict):
        return "{" + ", ".join(f"{key_text(k)}: {flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(flow(x) for x in v) + "]"
    return scalar(v)


def _flowable(v):
    return not isinstance(v, (dict, list)) or len(flow(v)) <= FLOW_MAX


def block(v, indent):
    """Lines (no newline) of a non-empty mapping or sequence written in block style at `indent` spaces."""
    pad = " " * indent
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x and not _flowable(x):
                out.append(f"{pad}{key_text(k)}:")
                out += block(x, indent + 2)
            else:
                out.append(f"{pad}{key_text(k)}: {flow(x)}")
    else:
        for x in v:
            if isinstance(x, dict) and x:   # a mapping item is written as a block mapping, like the nodes and rules of a Pack
                inner = block(x, indent + 2)
                out.append(f"{pad}- {inner[0].lstrip()}")
                out += inner[1:]
            elif isinstance(x, list) and x and not _flowable(x):
                out.append(f"{pad}-")
                out += block(x, indent + 2)
            else:
                out.append(f"{pad}- {flow(x)}")
    return out


class _Rewriter:
    def __init__(self, text):
        self.text = text
        self.edits = []   # (start, end, replacement)
        self.loader = strict_yaml._Loader("")

    # --- positions
    def line_start(self, i):
        return self.text.rfind("\n", 0, i) + 1

    def line_end(self, i):
        """Index just after the newline of the line holding character i-1 (the end of a value ending at i)."""
        j = self.text.find("\n", max(i - 1, 0))
        return len(self.text) if j < 0 else j + 1

    def content_end(self, n):
        if isinstance(n, yaml.ScalarNode) or n.flow_style:
            return n.end_mark.index
        kids = [x for kv in n.value for x in kv] if isinstance(n, yaml.MappingNode) else n.value
        return max(self.content_end(k) for k in kids)

    def value(self, n):
        if isinstance(n, yaml.MappingNode):
            return {self.value(k): self.value(v) for k, v in n.value}
        if isinstance(n, yaml.SequenceNode):
            return [self.value(x) for x in n.value]
        return self.loader.construct_object(n)

    def dash_start(self, item):
        """Start of the line of a block sequence item's dash."""
        return self.line_start(item.start_mark.index)

    def dash_col(self, item):
        ls = self.dash_start(item)
        seg = self.text[ls:item.start_mark.index]
        if "-" not in seg:
            raise WriteError(f"{item.start_mark.line + 1}줄: 목록 항목의 '-'를 찾지 못했습니다")
        return seg.index("-")

    # --- edits
    def edit(self, n, new):
        if _same(self.value(n), new):
            return
        if isinstance(n, yaml.MappingNode) and isinstance(new, dict) and new:
            return self.edit_flow_map(n, new) if n.flow_style else self.edit_block_map(n, new)
        if isinstance(n, yaml.SequenceNode) and isinstance(new, list) and new and not n.flow_style:
            return self.edit_block_seq(n, new)
        self.replace(n, new)

    def replace(self, n, new):
        if isinstance(n, yaml.ScalarNode) or n.flow_style:
            end = n.end_mark.index
            text = flow(new)
            if isinstance(n, yaml.ScalarNode) and n.style in ("|", ">"):
                end = self.line_end(self.content_end(n))   # a block scalar runs to the end of its last line
                text += "\n"
            self.edits.append((n.start_mark.index, end, text))
            return
        start, end = n.start_mark.index, self.line_end(self.content_end(n))
        col = start - self.line_start(start)
        if isinstance(new, (dict, list)) and new:
            lines = block(new, col)
            text = lines[0][col:] + "".join("\n" + l for l in lines[1:]) + "\n"
        else:
            text = flow(new) + "\n"
        self.edits.append((start, end, text))

    def edit_block_map(self, n, new):
        pairs = [(self.value(k), k, v) for k, v in n.value]
        old = {k for k, _, _ in pairs}
        for k, kn, vn in pairs:
            if k in new:
                self.edit(vn, new[k])
            else:
                self.edits.append((self.line_start(kn.start_mark.index), self.line_end(self.content_end(vn)), ""))
        added = {k: v for k, v in new.items() if k not in old}
        if added:
            col = pairs[0][1].start_mark.column
            at = self.line_end(self.content_end(pairs[-1][2]))
            self.edits.append((at, at, "".join(l + "\n" for l in block(added, col))))

    def edit_flow_map(self, n, new):
        pairs = [(self.value(k), k, v) for k, v in n.value]
        if [k for k, _, _ in pairs] == [k for k in new if k in {p[0] for p in pairs}] and set(new) == {k for k, _, _ in pairs}:
            for k, _, vn in pairs:
                self.edit(vn, new[k])
            return
        # keys added or removed: write the mapping again, keeping the original text of kept keys and unchanged values
        # (in the new value's key order, so a rewritten value reads the way it was given)
        kept = {k: (kn, vn) for k, kn, vn in pairs}
        parts = []
        for k, v in new.items():
            if k in kept:
                kn, vn = kept[k]
                vtext = self.text[vn.start_mark.index:self.content_end(vn)] if _same(self.value(vn), v) else flow(v)
                parts.append(self.text[kn.start_mark.index:kn.end_mark.index] + ": " + vtext)
            else:
                parts.append(f"{key_text(k)}: {flow(v)}")
        self.edits.append((n.start_mark.index, n.end_mark.index, "{" + ", ".join(parts) + "}"))

    def edit_block_seq(self, n, new):
        old = n.value
        ident = lambda v: ("id", v["id"]) if isinstance(v, dict) and isinstance(v.get("id"), str) else ("v", _canon(v))
        a, b = [ident(self.value(x)) for x in old], [ident(v) for v in new]
        pairs = _pair_gaps(_lcs(a, b), old, new, a, b)
        col = self.dash_col(old[0])
        matched_old = {i for i, _ in pairs}
        for i, item in enumerate(old):
            if i not in matched_old:
                self.edits.append((self.dash_start(item), self.line_end(self.content_end(item)), ""))
        for i, j in pairs:
            self.edit(old[i], new[j])
        # new items go right after the old item before them (or before the first old item)
        prev_old = {}   # j -> index of the old item it follows (-1: before all)
        last = -1
        jmap = dict((j, i) for i, j in pairs)
        for j in range(len(new)):
            if j in jmap:
                last = jmap[j]
            else:
                prev_old.setdefault(last, []).append(j)
        for i, js in prev_old.items():
            at = self.dash_start(old[0]) if i < 0 else self.line_end(self.content_end(old[i]))
            text = "".join(l + "\n" for j in js for l in block([new[j]], col))
            self.edits.append((at, at, text))

    def result(self):
        out, pos = [], len(self.text)
        for s, e, t in sorted(self.edits, key=lambda x: (x[0], x[1]), reverse=True):
            if e > pos:
                raise WriteError("겹치는 수정이 있습니다")
            out.append(self.text[e:pos]); out.append(t); pos = s
        out.append(self.text[:pos])
        return "".join(reversed(out))


def _pair_gaps(pairs, old, new, a, b):
    """Between two matched items, an equal number of unmatched old and new items without an `id` are the same items with
    changed values: pair them by position so they are edited in place (a rule whose value changed stays on its line)."""
    out, pi, pj = [], -1, -1
    for i, j in pairs + [(len(old), len(new))]:
        gi, gj = list(range(pi + 1, i)), list(range(pj + 1, j))
        if len(gi) == len(gj) and all(a[x][0] == "v" and b[y][0] == "v" for x, y in zip(gi, gj)):
            out += list(zip(gi, gj))
        if i < len(old):
            out.append((i, j))
        pi, pj = i, j
    return out


def _lcs(a, b):
    m, n = len(a), len(b)
    L = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            L[i][j] = L[i + 1][j + 1] + 1 if a[i] == b[j] else max(L[i + 1][j], L[i][j + 1])
    i = j = 0
    out = []
    while i < m and j < n:
        if a[i] == b[j]:
            out.append((i, j)); i += 1; j += 1
        elif L[i + 1][j] >= L[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def rewrite(text, new):
    """Return text whose strict-YAML value is `new`, changing only what differs from the value of `text`."""
    strict_yaml.load(text)   # the original must be strict YAML (raises StrictYAMLError)
    root = yaml.compose(text, Loader=strict_yaml._Loader)
    rw = _Rewriter(text)
    rw.edit(root, new)
    out = rw.result()
    try:
        got, _ = strict_yaml.load(out)
    except strict_yaml.StrictYAMLError as e:
        raise WriteError(f"고친 글을 다시 읽을 수 없습니다 — {e}")
    if not _same(got, new):
        raise WriteError("고친 글을 다시 읽은 값이 바라는 값과 다릅니다")
    return out
