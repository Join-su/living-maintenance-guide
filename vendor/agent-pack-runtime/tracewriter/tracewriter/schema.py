"""Contract 2 event schema (trace contract 2·4), JSON Schema draft 2020-12 (the writer uses Draft202012Validator). The writer checks
every event against it before anything is stored."""
REF = {"type": "object", "required": ["ref", "size", "media_type"], "additionalProperties": False,
       "properties": {"ref": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}, "size": {"type": "integer", "minimum": 0},
                      "media_type": {"type": "string", "minLength": 1}}}
OPT_REF = {"anyOf": [REF, {"type": "null"}]}
HEX16 = "^[0-9a-f]{16}$"
TEXT = {"type": "string", "minLength": 1}
OPT_TEXT = {"type": ["string", "null"]}
ERROR = {"type": ["object", "null"]}


def _body(required, props=None):
    return {"type": "object", "required": required, "properties": props or {}}


BODIES = {
    "run_started": _body(["origin", "source_kind", "pack_id", "pack_revision", "data_release_id", "release_bindings", "runtime_version",
                          "graph", "manifest", "input_ref", "retention_class"],
                         {"origin": {"enum": ["evaluation", "deployment"]}, "source_kind": {"enum": ["agent_pack", "langgraph"]},
                          "evaluation_run_id": OPT_TEXT, "deployment_id": OPT_TEXT, "pack_id": TEXT, "pack_revision": TEXT,
                          "data_release_id": {"anyOf": [{"type": "string", "pattern": "^drs:[0-9a-f]{16}$"}, {"type": "null"}]},
                          "release_bindings": {"type": "object"}, "runtime_version": TEXT, "graph": TEXT, "manifest": {"type": "object"},
                          "input_ref": REF, "retention_class": {"enum": ["standard", "dev"]}}),
    "node_started": _body(["node_path", "iteration", "input_ref"], {"node_path": TEXT, "iteration": {"type": "integer", "minimum": 1}, "input_ref": REF}),
    "node_ended": _body(["node_path", "iteration", "status", "output_ref", "ms", "error", "meta"],
                        {"node_path": TEXT, "iteration": {"type": "integer", "minimum": 1}, "status": {"enum": ["done", "error", "skipped"]},
                         "output_ref": OPT_REF, "ms": {"type": ["integer", "null"]}, "error": ERROR, "meta": {"type": "object"}}),
    "review_requested": _body(["node_path", "review_id", "subject_ref", "allowed_actions", "editable"],
                              {"node_path": TEXT, "review_id": TEXT, "subject_ref": REF, "allowed_actions": {"type": "array"}, "editable": {"type": "array"}}),
    "review_recorded": _body(["review_id", "action", "actor", "actor_kind", "reason", "edited_fields", "revalidation"],
                             {"review_id": TEXT, "action": TEXT, "actor": TEXT, "actor_kind": {"enum": ["human", "fixture", "model", "auto"]},
                              "role": OPT_TEXT, "reason": {"type": "string"}, "edited_fields": {"type": "array"}, "edited_value_ref": OPT_REF,
                              "revalidation": {"type": "array"}}),
    "artifact_written": _body(["node_path", "format", "sha256", "location", "size"],
                              {"node_path": TEXT, "format": TEXT, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "location": TEXT,
                               "size": {"type": "integer", "minimum": 0}}),
    "run_ended": _body(["status", "outcome", "output_ref", "error", "ms", "tokens_in", "tokens_out"],
                       {"status": {"enum": ["succeeded", "failed", "cancelled"]}, "outcome": OPT_TEXT, "output_ref": OPT_REF, "error": ERROR,
                        "ms": {"type": ["integer", "null"]}, "tokens_in": {"type": ["integer", "null"]}, "tokens_out": {"type": ["integer", "null"]}}),
}

EVENT = {
    "type": "object",
    "required": ["contract", "run_id", "seq", "trace_id", "span_id", "parent_span_id", "type", "at", "node_id", "body"],
    "additionalProperties": False,
    "properties": {
        "contract": {"const": 2},
        "run_id": {"type": "string", "pattern": "^run_[0-9A-HJKMNP-TV-Z]{26}$"},
        "seq": {"type": "integer", "minimum": 0},
        "trace_id": {"type": "string", "pattern": "^(?!0{32})[0-9a-f]{32}$"},
        "span_id": {"type": "string", "pattern": HEX16},
        "parent_span_id": {"anyOf": [{"type": "string", "pattern": HEX16}, {"type": "null"}]},
        "type": {"enum": list(BODIES)},
        "at": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"},
        "node_id": OPT_TEXT,
        "body": {"type": "object"},
    },
    "allOf": [{"if": {"properties": {"type": {"const": t}}}, "then": {"properties": {"body": b}}} for t, b in BODIES.items()],
}
