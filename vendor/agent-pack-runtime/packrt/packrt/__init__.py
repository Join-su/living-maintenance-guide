"""Agent Pack runtime (spec docs/agent-pack-spec-v0.md v0.3). `packrt.ir` reads and validates pack.yaml and computes the
pack revision (used by the LLMOps server); `packrt.run` builds and runs the graph (used only inside the Pack's own Python)."""
__version__ = "0.15.0"
IR_VERSION = 1


def release_line(runtime):
    """packrt.release_line — the same as packrt.trace.release_line (EV-006 CHG-R12, contract E)."""
    from .trace import release_line as _line
    return _line(runtime)
