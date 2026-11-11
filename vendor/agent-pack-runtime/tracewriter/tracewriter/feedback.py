"""Human feedback record rules (EV-004, D-182 · deploy 052 5절): what the Deployment Server may write to trace.feedback through
TraceWriter.record_feedback. check_feedback() checks the record itself, without a database — whether the run exists, succeeded
and allowed that action at that node is the Deployment Server's check. The label list is a copy of LLMOps labels-v1
(llmops autodiag.LABELS); its name goes into every row so a later list can be told apart.
EV-014 (contract 2): check_check_record — the outside of one person check record for TraceWriter.record_check_record."""
import json
import re

from .redact import redact, redact_text

FEEDBACK_CONTRACT = 1
LABEL_LIST_VERSION = "labels-v1"
LABELS = ("검색 못 함", "근거 부족", "규칙 실패", "LLM 출력 틀림", "정상 동작(escalation 맞음)", "Pack 밖 원인")
OK_LABELS = ("정상 동작(escalation 맞음)",)
ACTIONS = ("approve", "edit", "escalate", "reject")

FIELDS = ("feedback_id", "run_id", "source", "review_node", "node", "action", "label", "actor", "reason", "edited_value",
          "retention_class", "at")   # the trace.feedback column order (deploy DDL draft); problems come back in this order
REQUIRED = ("feedback_id", "run_id", "action", "actor", "reason", "retention_class", "at")
FEEDBACK_ID_RE = re.compile(r"^fb_[0-9A-HJKMNP-TV-Z]{26}$")
RUN_ID_RE = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
NODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")   # the LLMOps alias rule (llmops deploying.ALIAS_RE)
PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z_][A-Za-z0-9_]*|[0-9]+))*$")
MAX_REASON, MAX_EDITS, MAX_PATH, MAX_NODE = 500, 50, 200, 64


def check_feedback(record, secrets=()):
    """[(JSON path, message)] for every problem of one feedback record (the record_feedback arguments as a dict); empty = fine.
    secrets: exact secret values to treat as key-shaped too (redact-1 = those values + the common key formats); TraceWriter passes
    its own. A path never quotes a key: an edited path is shown redacted and cut at 200 characters."""
    if not isinstance(record, dict):
        return [("$", "피드백 기록은 객체입니다")]
    out = []
    say = lambda field, msg: out.append((f"$.{field}", msg))
    for f in FIELDS:
        v = record.get(f)
        if v is None:
            if f in REQUIRED:
                say(f, "필요한 칸입니다")
            elif f == "label" and record.get("action") in ("edit", "escalate", "reject"):
                say(f, "edit · escalate · reject에는 '정상 동작'을 뺀 labels-v1 라벨 하나가 필요합니다")
            elif f == "edited_value" and record.get("action") == "edit":
                say(f, "edit에는 고친 값 {경로: 값} 1 ~ 50개가 필요합니다")
            continue
        if f == "feedback_id" and not (isinstance(v, str) and FEEDBACK_ID_RE.match(v)):
            say(f, "피드백 id(fb_ + ULID) 모양이 아닙니다")
        elif f == "run_id" and not (isinstance(v, str) and RUN_ID_RE.match(v)):
            say(f, "실행 id(run_ + ULID) 모양이 아닙니다")
        elif f == "source" and v != "deploy_api":
            say(f, "source는 deploy_api입니다")
        elif f in ("review_node", "node") and not (isinstance(v, str) and NODE_RE.match(v) and len(v) <= MAX_NODE):
            say(f, "node id 모양이 아닙니다")
        elif f == "action" and v not in ACTIONS:
            say(f, "approve · edit · escalate · reject 중 하나입니다")
        elif f == "label":
            if v not in LABELS:
                say(f, "labels-v1 라벨이 아닙니다")
            elif record.get("action") == "approve" and v not in OK_LABELS:
                say(f, "approve에는 라벨이 없거나 '정상 동작(escalation 맞음)'만 씁니다")
            elif record.get("action") in ("edit", "escalate", "reject") and v in OK_LABELS:
                say(f, "edit · escalate · reject에는 '정상 동작'을 뺀 labels-v1 라벨 하나가 필요합니다")
        elif f == "actor":
            if not (isinstance(v, str) and ALIAS_RE.match(v) and not v.isdigit()):
                say(f, "별명 ID(영문 소문자로 시작, 소문자·숫자·-·_, 2~32자)로 적습니다 — 실명·연락처는 넣지 않습니다")
            elif redact_text(v, secrets) != v:
                say(f, "키 모양 글은 별명으로 쓸 수 없습니다")
        elif f == "reason" and not (isinstance(v, str) and 1 <= len(v.strip()) <= MAX_REASON):
            say(f, "이유는 1 ~ 500자 글입니다")
        elif f == "edited_value":
            if record.get("action") != "edit":
                say(f, "edit 밖의 동작에는 고친 값을 두지 않습니다")
            elif not (isinstance(v, dict) and 1 <= len(v) <= MAX_EDITS):
                say(f, "edit에는 고친 값 {경로: 값} 1 ~ 50개가 필요합니다")
            else:
                for path in v:
                    shown = redact_text(path, secrets)[:MAX_PATH] if isinstance(path, str) else type(path).__name__
                    where = f'edited_value["{shown}"]'
                    if not (isinstance(path, str) and PATH_RE.match(path) and len(path) <= MAX_PATH):
                        say(where, "칸 경로 모양이 아닙니다")
                    elif redact_text(path, secrets) != path:   # EV-004 D-1: the paths go to the database as they are
                        say(where, "키 모양 글은 칸 경로로 쓸 수 없습니다")
        elif f == "retention_class" and v not in ("standard", "dev"):
            say(f, "보존 등급은 standard 또는 dev입니다")
        elif f == "at" and not (isinstance(v, str) and AT_RE.match(v)):
            say(f, "시각(ISO, Z로 끝남) 모양이 아닙니다")
    for k in record:
        if k not in FIELDS:
            say(k, "모르는 칸입니다")
    return out


# ---------------------------------------------------------------- person check records (EV-014, D-412 · D-413, contract 2)
# One person check record document (the app's c1a15.check-record.v1, one per session, sent once after the lead's last decision)
# as one trace.feedback row of kind check_record — record_key · session_id · signals and the redacted document as a payload file.
# The outside only: the document's meaning (signals counted, field meanings) is LLMOps' check, its schema the app's.
CHECK_RECORD_CONTRACT = 2
CHECK_RECORD_KIND = "check_record"
CHECK_RECORD_FIELDS = ("feedback_id", "run_id", "source", "record_key", "session_id", "signals", "record", "actor", "retention_class", "at")
CHECK_RECORD_REQUIRED = ("feedback_id", "run_id", "record_key", "session_id", "signals", "record", "actor", "retention_class", "at")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")   # record_key (CR-<report>) · session_id (S-A<alarm>)
SIGNAL_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_SIGNALS, MAX_RECORD_BYTES = 20, 1024 * 1024


def check_check_record(record, secrets=()):
    """[(JSON path, message)] for every problem of one check record (the record_check_record arguments as a dict); empty = fine.
    The document is counted after redaction (canonical JSON ≤ 1 MiB, the Deployment Server's limit); a record_key inside it must
    be the one given. A key-shaped value is never quoted. Every pattern must match the whole value (fullmatch — no trailing line
    break), and key-shaped text (redact-1 with the writer's secrets) is refused in the free-text column values: record_key,
    session_id, each signal and actor (E3-A1 B1)."""
    if not isinstance(record, dict):
        return [("$", "체크 기록은 객체입니다")]
    out = []
    say = lambda field, msg: out.append((f"$.{field}", msg))
    for f in CHECK_RECORD_FIELDS:
        v = record.get(f)
        if v is None:
            if f in CHECK_RECORD_REQUIRED:
                say(f, "필요한 칸입니다")
            continue
        if f == "feedback_id" and not (isinstance(v, str) and FEEDBACK_ID_RE.fullmatch(v)):
            say(f, "피드백 id(fb_ + ULID) 모양이 아닙니다")
        elif f == "run_id" and not (isinstance(v, str) and RUN_ID_RE.fullmatch(v)):
            say(f, "실행 id(run_ + ULID) 모양이 아닙니다")
        elif f == "source" and v != "deploy_api":
            say(f, "source는 deploy_api입니다")
        elif f in ("record_key", "session_id"):
            if not (isinstance(v, str) and KEY_RE.fullmatch(v)):
                say(f, "영문 · 숫자로 시작하는 1 ~ 128자(영문 · 숫자 · . _ : -)입니다")
            elif redact_text(v, secrets) != v:
                say(f, "키 모양 글은 쓸 수 없습니다")
        elif f == "signals":
            if not (isinstance(v, list) and len(v) <= MAX_SIGNALS):
                say(f, f"신호 id 목록(0 ~ {MAX_SIGNALS}개)입니다")
            elif len(set(map(str, v))) != len(v):
                say(f, "같은 신호 id가 두 번 있습니다")
            else:
                for i, s in enumerate(v):
                    if not (isinstance(s, str) and SIGNAL_RE.fullmatch(s)):
                        say(f"{f}[{i}]", "신호 id(영문 · 숫자 · _ -, 1 ~ 32자) 모양이 아닙니다")
                    elif redact_text(s, secrets) != s:
                        say(f"{f}[{i}]", "키 모양 글은 신호 id로 쓸 수 없습니다")
        elif f == "record":
            if not isinstance(v, dict):
                say(f, "문서는 객체입니다")
            elif "record_key" in v and v["record_key"] != record.get("record_key"):
                say("record.record_key", "문서 안 record_key가 인자와 다릅니다")
            else:
                try:
                    red = json.dumps(redact(v, secrets=secrets)[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
                                     allow_nan=False)
                except (TypeError, ValueError):   # a key that is not a string next to string keys (E3-A1 N7) · NaN / Infinity (A2-N2)
                    say(f, "문서를 표준 JSON으로 쓸 수 없습니다(칸 이름은 글 · NaN · Infinity 없음)")
                else:
                    if len(red.encode("utf-8")) > MAX_RECORD_BYTES:
                        say(f, f"문서가 가린 뒤 {MAX_RECORD_BYTES} 바이트를 넘습니다")
        elif f == "actor":
            if not (isinstance(v, str) and ALIAS_RE.fullmatch(v) and not v.isdigit()):
                say(f, "별명 ID(영문 소문자로 시작, 소문자·숫자·-·_, 2~32자)로 적습니다 — 실명·연락처는 넣지 않습니다")
            elif redact_text(v, secrets) != v:
                say(f, "키 모양 글은 별명으로 쓸 수 없습니다")
        elif f == "retention_class" and v not in ("standard", "dev"):
            say(f, "보존 등급은 standard 또는 dev입니다")
        elif f == "at" and not (isinstance(v, str) and AT_RE.fullmatch(v)):
            say(f, "시각(ISO, Z로 끝남) 모양이 아닙니다")
    for k in record:
        if k not in CHECK_RECORD_FIELDS:
            say(k, "모르는 칸입니다")
    return out
