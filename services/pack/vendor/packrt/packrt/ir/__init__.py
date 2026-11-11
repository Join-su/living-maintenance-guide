"""Pack loading for ir_version 1 (spec 16.1 stages 0-2) and the pack revision (spec 3.1.1).

    result = packrt.ir.load_pack(folder)
    result.ok, result.errors, result.warnings  # problems: {"stage", "line", "path", "message"}
    result.doc, result.lines, result.revision, result.raw_files
Never executes Pack code: custom functions are only parsed."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from . import revision as _revision
from . import semantic, strict_yaml

SCHEMA_PATH = Path(__file__).with_name("pack.schema.json")
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@dataclass
class PackLoad:
    folder: str
    ok: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    doc: dict = None
    lines: dict = field(default_factory=dict)
    revision: str = None
    raw_files: dict = field(default_factory=dict)

    def problem(self, stage, path, message, level="error"):
        item = {"stage": stage, "line": strict_yaml.line_of(self.lines, path) if path is not None else None,
                "path": "/".join(str(p) for p in path) if path else "", "message": message}
        (self.errors if level == "error" else self.warnings).append(item)


def _schema_message(e):
    where = "/".join(str(p) for p in e.absolute_path) or "(최상위)"
    if e.validator == "additionalProperties":
        known, pats = set(e.schema.get("properties", {})), list(e.schema.get("patternProperties", {}))
        extra = sorted(k for k in e.instance if k not in known and not any(re.match(pp, k) for pp in pats)) if isinstance(e.instance, dict) else []
        return f"{where}: 정의되지 않은 키 {', '.join(repr(k) for k in extra) or ''}"
    if e.validator == "required":
        return f"{where}: 필요한 키가 없습니다 — {e.message.split(' is a required')[0]}"
    return f"{where}: {e.message}"


def load_pack(folder):
    folder = Path(folder)
    res = PackLoad(folder=str(folder))
    pack_file = folder / "pack.yaml"
    if not pack_file.is_file():
        res.problem("read", None, "pack.yaml이 없습니다")
        return res
    # stage 0: strict YAML
    try:
        res.doc, res.lines = strict_yaml.load(pack_file.read_text(encoding="utf-8"))
    except strict_yaml.StrictYAMLError as e:
        res.errors.append({"stage": "yaml", "line": e.line, "path": "", "message": e.message})
        return res
    except UnicodeDecodeError:
        res.problem("yaml", None, "pack.yaml이 UTF-8 글이 아닙니다")
        return res
    # stage 1: JSON Schema
    errs = sorted(_VALIDATOR.iter_errors(res.doc), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        for e in errs:
            res.problem("schema", tuple(e.absolute_path), _schema_message(e))
        return res
    # stage 2: semantic checks
    for level, path, msg in semantic.check(res.doc, folder, res.lines):
        res.problem("semantic", path, msg, level)
    if res.errors:
        return res
    try:
        res.revision, res.raw_files = _revision.compute(folder)
    except (strict_yaml.StrictYAMLError, ValueError, UnicodeDecodeError) as e:
        res.problem("revision", None, f"revision을 계산할 수 없습니다 — {e}")
        return res
    res.ok = True
    return res


compute_revision = _revision.compute
strict_schema_problems = semantic.strict_schema_problems   # EV-002 CHG-L03: what OpenAI strict structured output would not take
from .fixedflow import check_fixed_flow, fixed_flow  # noqa: E402  EV-016 (llmops EV-069 PK-1): needs load_pack above
from .options import node_options  # noqa: E402  0.15.0 (D-476): function node option declarations (needs load_pack above)
