"""사람 피드백 받기(VFE EV-005 지시 041, D-180 (A) · D-182): POST /runs/{run_id}/feedback의 본문 검사와 검토 node 찾기.

실행은 다시 돌리지 않는다. 기록은 공유 기록기 tracewriter의 record_feedback(0.1.3~)이 trace.feedback 한 줄로 쓴다.
배포단은 실행 맥락(실행 있음 · succeeded · 그 검토 node의 허용 동작 · 고칠 칸)을 보고, 칸 자체 규칙은 tracewriter check_feedback과
같은 값으로 먼저 본다(답장 052-01 Q3: tracewriter 0.1.3 고정 전에는 이 상수, tests/test_feedback_api.py가 tracewriter · llmops 값과 대조)."""
import json
import re

from tracewriter import redact, redact_text

LABEL_LIST_VERSION = "labels-v1"
LABELS = ("검색 못 함", "근거 부족", "규칙 실패", "LLM 출력 틀림", "정상 동작(escalation 맞음)", "Pack 밖 원인")   # llmops autodiag.LABELS
OK_LABELS = ("정상 동작(escalation 맞음)",)
ACTIONS = ("approve", "edit", "escalate", "reject")
NO_REVIEW_ACTIONS = ("approve", "escalate", "reject")   # 검토 node 없는 실행(no_issue) — 고칠 칸이 없어 edit 없음(Q1)
BODY_FIELDS = ("action", "label", "actor", "reason", "node", "edits")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")   # llmops deploying.ALIAS_RE
NODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z_][A-Za-z0-9_]*|[0-9]+))*$")
MAX_REASON, MAX_EDITS, MAX_PATH = 500, 50, 200
MAX_EDIT_BYTES = 5 * 1024 * 1024   # tracewriter MAX_PAYLOAD(가림 뒤)
# tracewriter check_feedback의 칸 → API 본문 칸
TW_FIELD = {"action": "action", "label": "label", "actor": "actor", "reason": "reason", "node": "node", "edited_value": "edits"}


def review_context(rt, review_node):
    """그 실행의 검토 node(없으면 None) → (허용 동작, 고칠 칸). 값은 불러온 Pack의 node params에서 읽는다."""
    if review_node is None:
        return NO_REVIEW_ACTIONS, ()
    for g in rt.doc["graphs"].values():
        for n in g.get("nodes") or []:
            if n.get("id") == review_node:
                p = n.get("params") or {}
                return tuple(p.get("actions") or ()), tuple(p.get("editable") or ())
    raise RuntimeError(f"검토 node를 Pack에서 찾지 못했습니다: {review_node}")


def node_ids(rt):
    return {n.get("id") for g in rt.doc["graphs"].values() for n in g.get("nodes") or []}


def check_body(body, *, allowed, editable, nodes, secrets):
    """[(JSON 경로, 문구)] — 비면 통과. 차례는 BODY_FIELDS 차례, 모르는 칸은 끝."""
    if not isinstance(body, dict):
        return [("$", "본문은 JSON 객체입니다")]
    out = []
    say = lambda where, msg: out.append((where, msg))
    action, label = body.get("action"), body.get("label")
    if action is None:
        say("$.action", "필요한 칸입니다")
    elif action not in ACTIONS:
        say("$.action", "approve · edit · escalate · reject 중 하나입니다")
    elif action not in allowed:
        say("$.action", f"이 실행의 검토에서 받지 않는 동작입니다(받는 동작: {' · '.join(allowed)})")
    if label is None:
        if action in ("edit", "escalate", "reject"):
            say("$.label", "edit · escalate · reject에는 '정상 동작'을 뺀 labels-v1 라벨 하나가 필요합니다")
    elif label not in LABELS:
        say("$.label", "labels-v1 라벨이 아닙니다")
    elif action == "approve" and label not in OK_LABELS:
        say("$.label", "approve에는 라벨이 없거나 '정상 동작(escalation 맞음)'만 씁니다")
    elif action in ("edit", "escalate", "reject") and label in OK_LABELS:
        say("$.label", "edit · escalate · reject에는 '정상 동작'을 뺀 labels-v1 라벨 하나가 필요합니다")
    actor = body.get("actor")
    if actor is None:
        say("$.actor", "필요한 칸입니다")
    elif not (isinstance(actor, str) and ALIAS_RE.match(actor) and not actor.isdigit()):
        say("$.actor", "별명 ID(영문 소문자로 시작, 소문자·숫자·-·_, 2~32자)로 적습니다 — 실명·연락처는 넣지 않습니다")
    elif redact_text(actor, secrets) != actor:
        say("$.actor", "키 모양 글은 별명으로 쓸 수 없습니다")
    reason = body.get("reason")
    if reason is None:
        say("$.reason", "필요한 칸입니다")
    elif not (isinstance(reason, str) and 1 <= len(reason.strip()) <= MAX_REASON):
        say("$.reason", f"이유는 1 ~ {MAX_REASON}자 글입니다")
    node = body.get("node")
    if node is not None and not (isinstance(node, str) and NODE_RE.match(node) and node in nodes):
        say("$.node", "이 Pack의 node id가 아닙니다")
    edits = body.get("edits")
    if edits is None:
        if action == "edit":
            say("$.edits", f"edit에는 고친 값 {{경로: 값}} 1 ~ {MAX_EDITS}개가 필요합니다")
    elif action != "edit":
        say("$.edits", "edit 밖의 동작에는 고친 값을 두지 않습니다")
    elif not (isinstance(edits, dict) and 1 <= len(edits) <= MAX_EDITS):
        say("$.edits", f"edit에는 고친 값 {{경로: 값}} 1 ~ {MAX_EDITS}개가 필요합니다")
    else:
        for path in edits:
            where = f'$.edits["{redact_text(path, secrets)[:MAX_PATH]}"]'
            if not (PATH_RE.match(path) and len(path) <= MAX_PATH):
                say(where, "칸 경로 모양이 아닙니다(이름 · 숫자를 . 으로 이음)")
            elif redact_text(path, secrets) != path:
                say(where, "키 모양 글은 칸 경로로 쓸 수 없습니다")
            elif path.split(".", 1)[0] not in editable:
                say(where, f"이 검토에서 고칠 수 있는 칸이 아닙니다(고칠 칸: {' · '.join(editable) or '없음'})")
        if not out and len(json.dumps(redact(edits, secrets=secrets)[0], ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), default=str).encode("utf-8")) > MAX_EDIT_BYTES:
            say("$.edits", "고친 값이 가림 뒤 5MB를 넘습니다")
    for k in body:
        if k not in BODY_FIELDS:
            say(f"$.{k}", "v1은 role을 받지 않습니다" if k == "role" else "모르는 칸입니다")
    return out


def check_shared(check_feedback, record, secrets):
    """tracewriter check_feedback(0.1.3~, 비밀 값 목록을 넘김 — RX-F1+)의 문제 중 본문 칸만 API 경로로. 서버가 정한 칸의 문제는
    여기서 422로 바꾸지 않는다(record_feedback이 trace_schema로 막음 = 서버 오류)."""
    out = []
    for where, msg in check_feedback(record, secrets):
        field, _, rest = where[2:].partition("[")
        if field in TW_FIELD:
            out.append((f"$.{TW_FIELD[field]}" + (f"[{rest}" if rest else ""), msg))
    return out


# ---------------------------------------------------------------- 판 2: 사람 체크 기록 문서(D-408 · D-412, 보고 202 · 206)
# 본문 {"kind": "check_record", "record": <문서>} — 문서 모양 · 신호 셈은 앱(주인) · 읽는 쪽 LLMOps 몫, 배포단은 겉만 본다.
CHECK_KIND = "check_record"
CHECK_FIELDS = ("kind", "record")
CHECK_ACTOR = "lead"                                    # 문서 = 파트장 마지막 결정 뒤 한 장(앱 EV-006 CH-4) — 역할 별명만
SCHEMA_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\.check-record\.v[0-9]{1,3}$")
RECORD_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
RUN_ID_RE = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
SIGNAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MAX_SIGNALS = 20                                       # 기록기 판 2 초안(packrt 111)과 같게 — 앱 check-signals-v1은 최대 5(001-p)
MAX_RECORD_BYTES = 1024 * 1024                          # 가림 뒤 정규화 JSON


def is_check_record(body):
    return isinstance(body, dict) and body.get("kind") == CHECK_KIND


def _memo_text(x, path="$.record"):
    """결정 3(D-412): 메모 글은 공유 기록에 넣지 않음 — `memo` 칸은 null만(있음/없음은 has_memo). 글이 든 첫 경로."""
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "memo" and v is not None:
                return f"{path}.{k}"
            hit = _memo_text(v, f"{path}.{k}")
            if hit:
                return hit
    elif isinstance(x, list):
        for i, v in enumerate(x):
            hit = _memo_text(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


def check_record_body(body, *, run_id, key, deployment_id, secrets):
    """(문제 [(경로, 문구)], 기록 칸 {record_key, session_id, signals}) — 문제가 비면 통과."""
    out = []
    say = lambda where, msg: out.append((where, msg))
    for k in body:
        if k not in CHECK_FIELDS:
            say(f"$.{k}", "모르는 칸입니다(체크 기록은 kind · record 둘)")
    rec = body.get("record")
    if not isinstance(rec, dict):
        say("$.record", "체크 기록 문서(JSON 객체)가 필요합니다")
        return out, None
    schema = rec.get("schema")
    if not (isinstance(schema, str) and SCHEMA_RE.match(schema)):
        say("$.record.schema", "'<앱>.check-record.v<판>' 꼴의 문서 이름이 필요합니다")
    rk = rec.get("record_key")
    if not (isinstance(rk, str) and RECORD_KEY_RE.match(rk)):
        say("$.record.record_key", "기록 키(영문으로 시작, 영문 · 숫자 · - · _, 2 ~ 64자)가 필요합니다")
    elif redact_text(rk, secrets) != rk:
        say("$.record.record_key", "키 모양 글은 기록 키로 쓸 수 없습니다")
    elif key != rk:
        say("header:Idempotency-Key", "체크 기록의 Idempotency-Key는 record_key와 같아야 합니다")
    runs = rec.get("runs")
    if not isinstance(runs, dict):
        say("$.record.runs", "runs 객체(deployment_id · start · report …)가 필요합니다")
    else:
        if runs.get("deployment_id") != deployment_id:
            say("$.record.runs.deployment_id", "이 배포 서버의 배포 id가 아닙니다")
        anchor = runs.get("report") or runs.get("start")
        if not (isinstance(anchor, str) and RUN_ID_RE.match(anchor)):
            say("$.record.runs", "붙을 Run(runs.report, 없으면 runs.start)이 필요합니다")
        elif anchor != run_id:
            say("path:run_id", "체크 기록은 runs.report(없으면 runs.start) Run 주소로 보냅니다")
    session = rec.get("session")
    sid = session.get("pack_session_id") if isinstance(session, dict) else None
    if not (isinstance(sid, str) and SESSION_RE.match(sid) and redact_text(sid, secrets) == sid):   # 기록기 판 2 초안: 필수(001-p)
        say("$.record.session.pack_session_id", "세션 id(session.pack_session_id)가 필요합니다(꼴 · 키 모양 글 0)")
    sigs = rec.get("signals", [])
    ids = []
    if not (isinstance(sigs, list) and len(sigs) <= MAX_SIGNALS):
        say("$.record.signals", f"신호 목록(0 ~ {MAX_SIGNALS}개)이 필요합니다")
    else:
        for i, s in enumerate(sigs):
            sid_ = s.get("id") if isinstance(s, dict) else None
            if not (isinstance(sid_, str) and SIGNAL_RE.match(sid_)):
                say(f"$.record.signals[{i}].id", "신호 id 꼴이 아닙니다")
            else:
                ids.append(sid_)
    memo = _memo_text(rec)
    if memo:
        say(memo, "메모 글은 공유 기록에 보내지 않습니다(has_memo만, D-412 결정 3)")
    if not out:
        size = len(json.dumps(redact(rec, secrets=secrets)[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        if size > MAX_RECORD_BYTES:
            say("$.record", "체크 기록이 가림 뒤 1MB를 넘습니다")
    meta = {"record_key": rk, "session_id": sid, "signals": sorted(set(ids))} if not out else None
    return out, meta
