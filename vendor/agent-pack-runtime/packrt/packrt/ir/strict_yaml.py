"""Stage 0 of loading (spec 16.1): strict YAML reading, before JSON Schema.

YAML 1.2-style booleans only (`true`/`false`); rejected: keys that are not strings, unquoted reserved words as keys
(`on`, `off`, `yes`, `no`, `y`, `n`, `true`, `false`, `null`, `~`), duplicate keys in one mapping, aliases, anchors and tags.
Returns the plain value and a map from JSON-pointer-like paths (tuples) to 1-based source lines, so later stages can
point at the line of a node."""
import re

import yaml

RESERVED_KEYS = {"on", "off", "yes", "no", "y", "n", "true", "false", "null", "~"}


class StrictYAMLError(Exception):
    def __init__(self, line, message):
        self.line, self.message = line, message
        super().__init__(f"{line}줄: {message}")


class _Loader(yaml.SafeLoader):
    pass


# drop YAML 1.1 bool words (yes/no/on/off/...) and keep only true/false
_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"] for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))


def _scan_forbidden(text):
    try:
        for tok in yaml.scan(text, Loader=_Loader):
            if isinstance(tok, (yaml.AliasToken, yaml.AnchorToken)):
                raise StrictYAMLError(tok.start_mark.line + 1, "YAML 별칭·앵커(& *)는 쓸 수 없습니다")
            if isinstance(tok, yaml.TagToken):
                raise StrictYAMLError(tok.start_mark.line + 1, "YAML 태그(!)는 쓸 수 없습니다")
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise StrictYAMLError(mark.line + 1 if mark else 1, f"YAML을 읽을 수 없습니다 — {getattr(e, 'problem', e)}")


def load(text):
    """Return (value, lines). Raises StrictYAMLError."""
    _scan_forbidden(text)
    loader = _Loader(text)
    try:
        node = loader.get_single_node()
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise StrictYAMLError(mark.line + 1 if mark else 1, f"YAML을 읽을 수 없습니다 — {getattr(e, 'problem', e)}")
    finally:
        loader.dispose()
    if node is None:
        raise StrictYAMLError(1, "빈 파일입니다")
    lines = {}

    def build(n, path):
        lines[path] = n.start_mark.line + 1
        if isinstance(n, yaml.MappingNode):
            out = {}
            for kn, vn in n.value:
                line = kn.start_mark.line + 1
                if not isinstance(kn, yaml.ScalarNode):
                    raise StrictYAMLError(line, "키는 글자여야 합니다(목록·mapping 키 불가)")
                if kn.style is None and kn.value in RESERVED_KEYS:
                    raise StrictYAMLError(line, f"따옴표 없는 '{kn.value}'는 키로 쓸 수 없습니다(참/거짓·null로 읽힘). 꼭 필요하면 \"{kn.value}\"처럼 감싸세요")
                key = loader.construct_object(kn)
                if not isinstance(key, str):
                    raise StrictYAMLError(line, f"키 {key!r}는 글자가 아닙니다. 따옴표로 감싸세요")
                if key in out:
                    raise StrictYAMLError(line, f"같은 키 '{key}'가 한 mapping에 두 번 있습니다")
                out[key] = build(vn, path + (key,))
            return out
        if isinstance(n, yaml.SequenceNode):
            return [build(v, path + (i,)) for i, v in enumerate(n.value)]
        return loader.construct_object(n)

    loader = _Loader("")
    try:
        return build(node, ()), lines
    finally:
        loader.dispose()


def line_of(lines, path):
    """Line of the closest existing ancestor of path."""
    path = tuple(path)
    while path and path not in lines:
        path = path[:-1]
    return lines.get(path, 1)
