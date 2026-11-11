"""Shared trace writer (trace contract 2, docs/contracts/trace-event-contract.md 6-8): the TraceSink implementation both servers
(LLMOps Server for evaluations, the Deployment Server for deployments) use to store run events in PostgreSQL `trace` with the
trace_writer account. It imports neither llmops nor packrt.

Public API: TraceWriter, RunSink, LineRelay, TraceWriteError, redact, redact_text, REDACTION_POLICY_VERSION,
sqlite_registration_check, new_run_id, new_trace_id, EVENT_SCHEMA, and for human feedback (EV-004, D-182) TraceWriter.record_feedback,
check_feedback, new_feedback_id, FEEDBACK_CONTRACT, LABEL_LIST_VERSION, LABELS, OK_LABELS. 0.1.4 (EV-014): the method
TraceWriter.record_check_record for person check records (contract 2); its outside check lives in tracewriter.feedback (not a
public name — the public list is as in 0.1.3)."""
__version__ = "0.1.4"

from .errors import TraceWriteError
from .feedback import FEEDBACK_CONTRACT, LABEL_LIST_VERSION, LABELS, OK_LABELS, check_feedback
from .ids import new_feedback_id, new_run_id, new_trace_id
from .redact import REDACTION_POLICY_VERSION, redact, redact_text
from .registration import sqlite_registration_check
from .relay import LineRelay
from .schema import EVENT as EVENT_SCHEMA
from .writer import RunSink, TraceWriter

__all__ = ["TraceWriter", "RunSink", "LineRelay", "TraceWriteError", "redact", "redact_text", "REDACTION_POLICY_VERSION",
           "sqlite_registration_check", "new_run_id", "new_trace_id", "EVENT_SCHEMA", "__version__",
           "check_feedback", "new_feedback_id", "FEEDBACK_CONTRACT", "LABEL_LIST_VERSION", "LABELS", "OK_LABELS"]
