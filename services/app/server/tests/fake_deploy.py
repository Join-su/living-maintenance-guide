"""시험용 가짜 배포 서버(EV-001 TB-12): 127.0.0.1 임시 포트, 배포 서버 API 계약 v0.9의 키 저장 규칙을 그대로 따른다.

- 같은 키 + 같은 본문 + 끝남 = 저장한 응답 그대로 + `Idempotent-Replayed: true`(성공 · 실행 뒤 실패 모두)
- 같은 키 + 같은 본문 + 실행 중 = 409 idempotency_in_progress(run_id 담김)
- 같은 키 + 다른 본문 = 422 idempotency_key_reused
- 실행 전 거부(422 invalid_input · 400)는 키를 쓰지 않는다
- 새 실행마다 시험이 넣은 동작(`plan`)을 하나씩 꺼내 쓴다: 성공 출력 · 오류(상태 · code · node retryable) · 지연(초)
- 받은 요청(방법 · 경로 · 머리글 · 본문)을 모두 `requests`에 남긴다
kind별 필수 · 금지 칸만 본다 — 정본 v2 전체 schema 검사는 받은 본문 증거를 c1a15-pack .venv로(VC-35).
"""

import dataclasses
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ML 길 alarm 출력 = c1a15-pack이 뜬 실제 Pack 출력(D-304 (3) · 보고 012-11, 사용자 모델)
# — 시험 기대값도 여기서 셈(EV-001 CH-28)
ML_ALARM_DATA = Path(__file__).parent / "fixtures" / "ml_alarm_outputs.jsonl"
# start = 사람이 승인한 40행(D-314)의 Pack v4 출력(EV-001 CH-35) — 첫 판정은 아래 앱 engine 계산(받은 similar로)
# — 값 = 장면 ③ 후보 p13-all-s0.5 40행(D-429, 실제 이력 벡터 DB ir-history-16d297d8 · c1a15-pack SL-6)
START_V4_DATA = Path(__file__).parent / "fixtures" / "start_v4_outputs.jsonl"
# 대역(40행 밖 시험 알람) = 옛 시험값 40행(ES-15 · D-363 ir-history-412ea43e)
# — 다른 시험의 기대(첫 지시 등)가 이 값으로 짜여 있음(D-396 시험 1에서 찾음)
STANDIN_DATA = Path(__file__).parent / "fixtures" / "start_standin_outputs.jsonl"
STAND_INS: list[str] = []  # 대역을 쓴 start 요청의 session_id(시험 기록 — CH-35 'stand_in: true'를 시험에만)

REQUIRED = {
    "alarm": {"event_card"},
    "start": {"session_id", "event_id", "group", "query", "events", "grades", "minutes"},
    "judge": {"session_id", "group", "similar", "events", "grades", "minutes"},
    "report": {"session_id", "group", "similar", "report_draft", "allowed_sections"},  # 약속 v3(EV-001 ES-10)
    "exception_split": {"session_id", "group", "text"},  # 약속 v10 2절 Pack run kind
    "exception_similar": {"session_id", "group", "exception"},  # 약속 v10 3절(v9 items 대신 예외 하나)
}
FORBIDDEN = {  # recent_work = start만(Pack 5e76c66 약속 v10 덧 · 다른 종류는 거절)
    "alarm": {"session_id", "group", "similar", "events", "grades", "minutes", "query", "recent_work"},
    "start": {"event_card", "settings"},
    "judge": {"event_card", "settings", "event_id", "query", "recent_work"},
    "report": {"event_card", "settings", "event_id", "query", "events", "grades", "minutes", "recent_work"},
    "exception_split": {"event_card", "settings", "event_id", "query", "events", "grades", "minutes", "similar"}
    | {"equipment_id", "occurred_at", "line_id", "items"},  # Pack schema: kind · session_id · group · text만
    "exception_similar": {"event_card", "settings", "event_id", "query", "events", "grades", "minutes", "text"}
    | {"items"},
}


def _sha(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class FakeDeploy:
    def __init__(self, deployment_id: str = "c1a15@v1-local", port: int = 0, host: str = "127.0.0.1"):
        self.deployment_id = deployment_id
        # 새 실행마다 앞에서 하나씩: {"output": {...}} | {"status": 500, "code": ..., "retryable": bool} + 선택 "delay"
        self.plan: list[dict] = []
        self.default: dict | None = None  # plan이 비었을 때(함수면 본문으로 계산)
        self.keys: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.executions = 0
        self.lock = threading.Lock()
        self.feedback: list[dict] = []  # 받은 사람 체크 기록(EV-006)
        self.feedback_keys: dict[str, dict] = {}
        self.feedback_fail: int | None = None
        self.revision = "sha256:fake-first"  # D-619 GET /info(사전 점검 KG 반영 뒤 대조)
        self.done = threading.Event()  # 실행 하나가 끝나 키에 저장될 때마다 set
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload, replayed=False):
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if replayed:
                    self.send_header("Idempotent-Replayed", "true")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):  # 클라이언트가 시간 초과로 끊음 — 실행은 끝까지 저장됨
                    pass

            def do_GET(self):
                if self.path == "/_keys":  # 단독 실행 때 시험 스크립트가 받은 키 순서를 증거로 떠 감(VC-42)
                    seen = [
                        {
                            "kind": r["body"].get("kind"),
                            "key": r["headers"].get("Idempotency-Key"),
                            "status": (fake.keys.get(r["headers"].get("Idempotency-Key")) or {}).get("status"),
                        }
                        for r in fake.posts()
                    ]
                    return self._send(200, seen)
                if self.path == "/info":
                    return self._send(
                        200, {"deployment_id": fake.deployment_id, "pack": {"pack_revision": fake.revision}}
                    )
                fake.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers), "body": None})
                rid = self.path.rsplit("/", 1)[-1]
                run = fake.runs.get(rid)
                if run is None:
                    return self._send(404, {"error": {"code": "run_not_found", "run_id": None}})
                self._send(200, run)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                key = self.headers.get("Idempotency-Key")
                try:
                    body = json.loads(raw)
                except ValueError:
                    return self._send(400, {"error": {"code": "invalid_json", "run_id": None}})
                if self.path == "/_revision":  # 단독 실행 때 새 그래프 배포 흉내(D-619)
                    fake.revision = body["revision"]
                    return self._send(200, {"revision": fake.revision})
                if self.path == "/_plan":  # 단독 실행 때 시험 스크립트가 다음 실행 동작을 넣음(화면 한 바퀴 VC-42)
                    fake.plan = list(body)
                    return self._send(200, {"plan": len(fake.plan)})
                if (
                    self.path == "/_answer"
                ):  # 단독 실행 때 Pack 옵션 흉내 바꿈(pack_answer 칸) · 빈 본문 = 기본(engine_answer)
                    fake.default = pack_answer(**body) if body else engine_answer
                    return self._send(200, {"answer": sorted(body)})
                fake.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body})
                if not key:
                    return self._send(400, {"error": {"code": "idempotency_key_missing", "run_id": None}})
                if self.path.endswith("/feedback"):  # 사람 체크 기록 한 장(EV-006 · deploy 202 꼴)
                    return self._send(*fake.take_feedback(self.path.split("/")[2], key, body))
                kind = body.get("kind")
                if kind not in REQUIRED or REQUIRED[kind] - body.keys() or FORBIDDEN[kind] & body.keys():
                    return self._send(422, {"error": {"code": "invalid_input", "where": "$", "run_id": None}})
                with fake.lock:
                    rec = fake.keys.get(key)
                    if rec is not None:
                        if rec["sha"] != _sha(body):
                            return self._send(422, {"error": {"code": "idempotency_key_reused", "run_id": None}})
                        if rec["status"] is None:
                            return self._send(
                                409,
                                {
                                    "error": {
                                        "code": "idempotency_in_progress",
                                        "retryable": True,
                                        "run_id": rec["run_id"],
                                    }
                                },
                            )
                        return self._send(rec["status"], rec["payload"], replayed=True)
                    fake.executions += 1
                    rid = f"run_{fake.executions:04d}"
                    fake.keys[key] = rec = {"sha": _sha(body), "status": None, "payload": None, "run_id": rid}
                    fake.runs[rid] = {"run_id": rid, "status": "running", "result": None, "error": None}
                    step = (
                        fake.plan.pop(0)
                        if fake.plan
                        else (fake.default(body) if callable(fake.default) else fake.default)
                    )
                if step and step.get("use_default"):  # 지연만 주고 출력은 기본 계산으로
                    step = {**(fake.default(body) if callable(fake.default) else fake.default or {}), **step}
                step = step or {"output": {}}
                if step.get("delay"):
                    time.sleep(step["delay"])
                if "output" in step:
                    summary = {"run_id": rid, "status": "succeeded", "deployment_id": fake.deployment_id}
                    status, payload = 201, {**summary, "result": {"output": step["output"], "grounding": {}}}
                    fake.runs[rid] = {**payload}
                else:
                    node = step.get("node_retryable", step.get("retryable", False))
                    err = {
                        "code": step["code"],
                        "run_id": rid,
                        "retryable": step.get("retryable", False),
                        "where": step.get("where", "$"),
                        "details": {"node_error": {"retryable": node, "code": step.get("node_code")}},
                    }
                    status, payload = step.get("status", 500), {"error": err}
                    fake.runs[rid] = {"run_id": rid, "status": "failed", "result": None, "error": err}
                rec["status"], rec["payload"] = status, payload
                fake.done.set()
                self._send(status, payload)

        self.server = ThreadingHTTPServer((host, port), H)  # host = 0.0.0.0은 compose 시험 서비스만(EV-001 CH-42)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def start_running(self, key: str, body: dict, run_id: str | None = "run_held") -> str | None:
        """다른 요청이 이 키로 실행 중인 모양(409 시험용). run_id=None이면 409에 run_id가 없는 모양."""
        self.keys[key] = {"sha": _sha(body), "status": None, "payload": None, "run_id": run_id}
        if run_id:
            self.runs[run_id] = {"run_id": run_id, "status": "running", "result": None, "error": None}
        return run_id

    def finish(self, key: str, *, output: dict | None = None, error: dict | None = None) -> None:
        """start_running으로 잡은 실행을 끝냄 — 키에 응답 저장 · GET /runs/{id}도 끝난 모양."""
        rec = self.keys[key]
        rid = rec["run_id"]
        if error is None:
            payload = {
                "run_id": rid,
                "status": "succeeded",
                "deployment_id": self.deployment_id,
                "result": {"output": output or {}, "grounding": {}},
            }
            rec["status"], rec["payload"] = 201, payload
            if rid:
                self.runs[rid] = payload
        else:
            err = {"run_id": rid, **error}
            rec["status"], rec["payload"] = 500, {"error": err}
            if rid:
                self.runs[rid] = {"run_id": rid, "status": "failed", "result": None, "error": err}

    def take_feedback(self, run_id: str, key: str, body: dict) -> tuple[int, dict]:
        """`POST /runs/{run_id}/feedback` — kind check_record만 · 같은 키 같은 본문 = 같은 응답 · 다른 본문 = 422.

        시험이 `feedback_fail`(상태 코드)를 넣으면 그 응답을 돌려줌(보내기 실패해도 승인 그대로 시험).
        """
        if self.feedback_fail:
            return self.feedback_fail, {"error": {"code": "internal_error", "run_id": run_id}}
        if body.get("kind") != "check_record" or not isinstance(body.get("record"), dict):
            return 422, {"error": {"code": "invalid_input", "where": "$.kind", "run_id": run_id}}
        if self.runs.get(run_id, {}).get("status") != "succeeded":
            return 404, {"error": {"code": "run_not_found", "run_id": run_id}}
        with self.lock:
            old = self.feedback_keys.get(key)
            if old is not None:
                if old["sha"] != _sha(body):
                    return 422, {"error": {"code": "idempotency_key_reused", "run_id": run_id}}
                return 201, old["reply"]
            reply = {"feedback_id": f"fb_{len(self.feedback) + 1:04d}", "run_id": run_id, "at": "2026-10-27T00:00:00Z"}
            self.feedback.append({"run_id": run_id, "key": key, "body": body})
            self.feedback_keys[key] = {"sha": _sha(body), "reply": reply}
            return 201, reply

    def gets(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "GET"]

    def posts(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]


def start_v4_rows(path: Path | None = None) -> list[dict]:
    """승인 40행(D-314 — 시연 10 + 카드 val 30, Pack v4 start 출력) + 입력 칸.

    만든 도구 = 기록 폴더 ES-15 make_start_fixture.py.
    """
    lines = (path or START_V4_DATA).read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def start_v4_row(body: dict) -> dict:
    """요청 (group · direction · 센서 5칸)과 같은 행.

    없으면 대역 자료(STANDIN_DATA)에서 같은 group · direction의 첫 행(대역, 40행 밖 시험 알람만 — CH-35).
    """
    q = body["query"]
    # 방향 null(약속 정본 enum, EV-009 파트장이 올린 경계 안 알람)은 증상군만
    same_way = lambda r: r["group"] == body["group"] and q["direction"] in (None, r["direction"])  # noqa: E731
    rows = [r for r in start_v4_rows() if same_way(r)]
    if not rows:
        raise KeyError(f"start 자료에 {body['group']} · {q['direction']} 행이 없음")
    same = [r for r in rows if r["sensor5"] == q["sensor_snapshot"]]
    if same:
        return same[0]
    STAND_INS.append(body.get("session_id"))
    # 단독 실행 기록(fake.log) — 방향 null(EV-009 파트장이 올린 경계 안 알람)은 승인 행이 원래 없어
    # 따로 적음(e2e 대역 0 검사 밖)
    mark = "no_direction" if q["direction"] is None else "stand_in"
    print(f"start {mark} session={body.get('session_id')}", file=sys.stderr, flush=True)
    stand = [r for r in start_v4_rows(STANDIN_DATA) if same_way(r)]
    return {**stand[0], "stand_in": True}


def ml_alarm_outputs(path: Path | None = None) -> dict[str, dict]:
    """시험 자료 읽기 한 곳(Q-22): jsonl 한 줄 = 한 행, 열쇠 row_id(= 각본 udi) → output.alarm(약속 v3 alarm 객체)."""
    lines = (path or ML_ALARM_DATA).read_text(encoding="utf-8").splitlines()
    return {str(r["row_id"]): r["output"]["alarm"] for r in (json.loads(x) for x in lines if x.strip())}


def engine_answer(body: dict) -> dict:
    """가짜가 판정을 계산하는 법: 앱 engine(fold → decide) 그대로.

    두 길 대조(VC-34)는 '본문이 충분하고 저장이 맞다'를 본다.

    실제 Pack과 같은 결과는 ES-09(1건) · VC-43(미리보기 = Pack) · 3단계 대조 몫.
    """
    from c1a15.api import deps
    from c1a15.engine import alarm_rules

    if body["kind"] == "alarm" and body["event_card"]["source_mode"] == "ml_predicted":
        card = body["event_card"]
        out = ml_alarm_outputs()[str(card["data_lineage"]["row_id"])]  # 앱은 row_id를 int로 보냄 — 형 맞춤
        return {"output": {"alarm": {**out, "event_id": card["event_id"]}}}
    if body["kind"] == "alarm":  # 경계 길: 앱 경계 규칙 그대로(계산 칸은 Pack과 같은 식)
        import math

        card = body["event_card"]
        s5 = card["sensor_snapshot"]
        derived = {
            "power_w": round(s5["torque_nm"] * s5["rotational_speed_rpm"] * 2 * math.pi / 60),
            "temp_diff_k": round(s5["process_temperature_k"] - s5["air_temperature_k"], 1),
        }
        hit = alarm_rules.detect({**s5, **derived}, deps.alarm_table())
        return {
            "output": {
                "alarm": {
                    "event_id": card["event_id"],
                    "alarm": hit is not None,
                    "group": hit["group"] if hit else None,
                    "group_source": "sensor_boundary" if hit else None,
                    "alarm_block": hit["alarm"] if hit else None,
                    "sensor_derived": derived,
                    "ml": None,
                    "no_alarm_reason": None,  # Pack 약속 v6(D-530 덧 3)
                }
            }
        }
    if body["kind"] == "report":  # Pack 가짜 provider(tools/fake/rule.py)와 같은 뜻: 초안 그대로 · 근거 검사 통과
        draft = body["report_draft"]
        return {
            "output": {
                "report": {
                    f: {"text": draft[f], "fallback": False, "attempts": 1, "reason": None} for f in ("cause", "action")
                }
            }
        }
    if body["kind"] == "exception_split":
        return {"output": split_answer(body["text"])}
    if body["kind"] == "exception_similar":
        return {"output": similar_answer(body)}
    return {"output": _judge_output(body)}


# 가짜 AI 나눔(약속 v10 2절): 글 줄(마침표 · 줄바꿈)의 '점검 …' → 점검 목록 · '조치 …' → 조치 목록 — 호출 0
# 가짜 비슷한 것 찾기(약속 v10 3절 꼴): Pack custom/similar.py와 같은 셈(글자 두 개 묶음 코사인 · 상위 3 · 최소 점수)을
# 예외 통째로 한 번 · 그 증상군 매뉴얼 점검 · 조치 절 + 가짜 등록 예외 절 하나(CL)에 — 같음 / 아님 둘 다
SIMILAR_EXCEPTION = {"CL": [("EX-R-0001", "필터 덮개 나사", "필터 덮개 고정 나사 네 개 체결 확인 나사를 새로 끼움")]}
# 가짜 등록 예외 지금 목록(Pack 1674865 꼴: matches 예외 줄 checks · actions 선택 칸 · 분 = 정수)
SIMILAR_EXCEPTION_LISTS = {
    "EX-R-0001": {
        "checks": [
            {
                "what": "필터 덮개 고정 나사",
                "how": "나사 네 개를 손으로 돌려 본다",
                "criteria": "네 개 모두 체결 = 정상",
                "minutes": 5,
            }
        ],
        "actions": [{"what": "나사를 새로 끼움", "parts": [], "minutes": 10}],
    }
}
SIMILAR_MIN = 0.15  # Pack similar.py MIN_SCORE(c1a15-pack 69ebdd1 · D-556 덧 5 · 6) — 빈 matches = 비슷한 것 없음


def _grams(t: str):
    import re
    from collections import Counter

    t = re.sub(r"[\s\W_]+", "", t or "")
    return Counter(t[i : i + 2] for i in range(len(t) - 1)) if len(t) > 1 else Counter(t)


def _cos(a, b) -> float:
    import math

    num = sum(v * b.get(k, 0) for k, v in a.items())
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def similar_answer(body: dict) -> dict:
    from c1a15.api import deps

    k = deps.manual()
    group = body["group"]
    secs = [x.section_id for x in k.checks.get(group, [])] + [x.action_section for x in k.causes.get(group, [])]
    pool = [("매뉴얼", s, k.sections[s]["title"], f"{k.sections[s]['title']} {k.sections[s]['text']}") for s in secs]
    pool += [("예외", no, name, f"{name} {txt}") for no, name, txt in SIMILAR_EXCEPTION.get(group, [])]
    ex = body["exception"]
    words = [ex.get("topic") or ""]
    words += [x for c in ex["checks"] for x in (c["what"], c.get("how") or "", c.get("criteria") or "")]
    words += [x for a in ex["actions"] for x in (a["what"], *(a.get("parts") or []))]
    q = _grams(" ".join(words))
    hits = sorted(((round(_cos(q, _grams(t)), 4), kd, no, nm) for kd, no, nm, t in pool), key=lambda h: (-h[0], h[2]))
    top = [
        {"kind": kd, "no": no, "name": nm, "score": sc, **(SIMILAR_EXCEPTION_LISTS.get(no, {}) if kd == "예외" else {})}
        for sc, kd, no, nm in hits[:3]
        if sc >= SIMILAR_MIN
    ]
    return {"exception_similar": {"matches": top}}


def split_answer(text: str) -> dict:
    """Pack 약속 v10 2절 꼴 흉내: {exception_split: {exception | null, reason}}
    — 점검 0 또는 조치 0이면 null + not_split.
    표지(Pack tools/fake/rule.py와 같은 이름): [시험:나눔없음] → not_split · [시험:꼴깨짐] → invalid_output."""
    import re

    if "[시험:나눔없음]" in text:
        return {"exception_split": {"exception": None, "reason": "not_split"}}
    if "[시험:꼴깨짐]" in text:
        return {"exception_split": {"exception": None, "reason": "invalid_output"}}
    lines = [x.strip() for x in re.split(r"[.\n]+", text) if x.strip()]
    checks = [x.removeprefix("점검").strip() for x in lines if x.startswith("점검")]
    actions = [x.removeprefix("조치").strip() for x in lines if x.startswith("조치")]
    if not checks and not actions:  # 머리말 없는 글 = 앞 줄 점검 · 뒤 줄 조치
        checks, actions = lines[:1], lines[1:2]
    checks = list(dict.fromkeys(x for x in checks if x))[:10]  # 같은 점검 합침 · 상한 10(Pack 거르기)
    actions = list(dict.fromkeys(x for x in actions if x))[:10]
    if not checks or not actions:  # 한쪽 0 = 나누지 못함(약속 v10 2절 · D-566 덧 1)
        return {"exception_split": {"exception": None, "reason": "not_split"}}
    topic = " ".join(checks[0].split()[:2] + checks[0].split()[3:4]).rstrip("을를")[:15]  # 가짜 주제(Pack 몫)
    # D-642 C 표지 [시험:문장] → 줄마다 지시 문장(Pack 몫 흉내 · 칸 이름 instruction)
    say = (lambda x, tail: {"instruction": f"{x} — {tail}"}) if "[시험:문장]" in text else (lambda x, tail: {})
    return {
        "exception_split": {
            "exception": {
                "topic": topic,
                "checks": [
                    {"what": c[:40], "how": c, "criteria": "정상 범위", "minutes": None, **say(c, "정상 범위면 정상")}
                    for c in checks
                ],
                "actions": [{"what": a[:40], "parts": [], "minutes": None, **say(a, "합니다")} for a in actions],
            },
            "reason": None,
        }
    }


def _with_desc(d: dict, k) -> dict:
    """Pack 986eaff 흉내: reason_facts.alternatives[].desc = 그 점검 절 매뉴얼 '판정 기준' 첫 문장(없는 절은 칸 0)."""
    import re

    rf = d.get("reason_facts") or {}
    alts = rf.get("alternatives")
    if not alts:
        return d

    def desc(sec):
        m = re.search(r"\*\*판정 기준\*\* (.+)", (k.sections.get(sec) or {}).get("text") or "")
        if not m:
            return None
        first = re.match(r"(.+?[.])(\s|$)", m.group(1).strip())
        return (first.group(1) if first else m.group(1)).strip()

    out = [{**a, **({"desc": x} if (x := desc(a.get("section"))) else {})} for a in alts]
    return {**d, "reason_facts": {**rf, "alternatives": out}}


def _judge_output(body: dict, opts: dict | None = None) -> dict:
    """start · judge 판정 출력. opts = Pack 옵션 흉내(EV-011): scoring · threshold · preview · manual_support · strip.

    opts가 없으면 지금 그대로(약속 v4 — preview · manual_support 칸 없음).
    """
    from c1a15.api import deps
    from c1a15.engine import exception, rules, session, switch

    opts = opts or {}
    k = deps.manual()
    row = start_v4_row(body) if body["kind"] == "start" else None  # start = Pack 리트리버 자리(약속 v4) — 승인 자료에서
    given = row["similar"] if row else body.get("similar", [])
    similar = [rules.Similar(s["record_id"], s.get("cause"), s["score"]) for s in given]
    ms = opts.get("manual_support") if row else body.get("manual_support")
    kw = {}
    if opts.get("threshold") is not None:
        kw["threshold"] = opts["threshold"]
    if opts.get("scoring") is not None:  # 켠 매뉴얼 몫 = start는 옵션 값, judge는 요청에 다시 담겨 온 값(약속 v5)
        sc = opts["scoring"]
        kw["scoring"] = dataclasses.replace(sc, manual=ms) if opts.get("use_manual") else sc

    # 약속 v8 흉내 = Pack custom/judge.py 차례(engine/exception 사본 · D-545 handoff README):
    # start는 관계를 맞춰(여기선 opts로 줌) 결과에 exception_guide, judge는 다시 담긴 exception_relation으로 같은 관계
    eg = opts.get("exception_guide")
    rec = opts.get(
        "exception_recent"
    )  # D-570 when.recent {section, days} — 그래프 records 없이 요청 recent_work만(Pack 5e76c66 _recent 사본)
    if eg and rec and body["kind"] == "start":
        graph = {"records": []}
        if not exception._recent(
            graph, rec, body.get("equipment_id"), body.get("occurred_at"), body.get("recent_work")
        ):
            eg = None  # 최근 정비 없는 같은 알람 = 추천 0
    ex = _ex_of(eg, opts) if eg else None
    if ex and body["kind"] == "judge" and body.get("exception_relation") != eg["relation_id"]:
        ex = None  # 관계 id가 다르면(그래프 버전이 바뀜) 예외 안내 없이 원래 판정

    def decide(events):
        # Pack 3502e29 judge: 예외 안내 뒤 첫 재가동 · 재가동 값은 예외 몫 — 원래 순서 판정(사본)에서 뺌(D-566 덧 2)
        state = session.fold(
            k, body["group"], similar, exception.base_events(events, ex) if ex else exception.without(events)
        )
        d = session.decide(state, k, body["grades"], body["minutes"], **kw)
        if ex:
            d = exception.step(d, ex, events, body["grades"], opts.get("exception_layer"), body["minutes"])
        if body["kind"] == "judge":  # 약속 v9 = Pack custom/judge.py 차례: 판정 뒤 점검 바꾸기 · 예외 진행 덧층(사본)
            d = switch.apply(
                d,
                k,
                {
                    "events": events,
                    "grades": body["grades"],
                    "group": body["group"],
                    "exception_relation": body.get("exception_relation"),
                },
            )
        d = _with_desc(d, k)  # Pack 986eaff graph_query.with_desc: 후보마다 점검 설명(판정 기준 첫 문장)
        return {x: v for x, v in d.items() if x not in ("probabilities", "evidence")} if opts.get("strip") else d

    out = {"decision": decide(body["events"])}
    if ex and body["kind"] == "start":
        out["exception_guide"] = eg
    if row:
        out["similar"], out["retrieval"] = row["similar"], row["retrieval"]
        if opts.get("preview"):  # 약속 v5 덧칸: 잠금 · 사전 확인을 했다고 놓은 판정(start에만)
            out["preview"] = decide([*body["events"], {"type": "lock"}, {"type": "precheck_done"}])
        if ms is not None:
            out["manual_support"] = ms
    return out


def _ex_of(eg: dict, opts: dict) -> tuple[dict, dict]:
    """그래프 꼴(d545 graph-shape 3 · 4절) 줄 흉내: (예외 관계 줄, 예외 절 줄) — exception_guide 칸에서 거꾸로 채움.
    관계 원인은 opts exception_cause(없으면 보고서 outcome unknown)."""
    x = {
        "id": eg["relation_id"],
        "step": eg["section"],
        "mode": "안내",
        "records": list(eg.get("basis") or []),
        "cause": opts.get("exception_cause"),
    }
    sec = {
        "no": eg["section"],
        "kind": "예외",
        "grade": eg.get("grade"),
        "name": eg.get("title"),
        "guide": eg.get("text"),
        "steps": list(eg.get("steps") or []),
        "criteria": eg.get("criteria"),
        "minutes": eg.get("minutes"),
        "records": list(eg.get("basis") or []),
        # 약속 v10: 그 절의 주제 · 점검 목록 · 조치 목록(있을 때만 — 없으면 옛 길)
        **{f: eg[f] for f in ("topic", "checks", "actions", "note") if eg.get(f)},
    }
    return x, sec


def pack_answer(**opts):
    """옵션을 바꾼 Pack 흉내(EV-011 V-11 · V-14 ~ V-17): FakeDeploy.default에 넣음 · alarm · report는 engine_answer."""

    def answer(body: dict) -> dict:
        if body["kind"] in ("start", "judge"):
            return {"output": _judge_output(body, opts)}
        return engine_answer(body)

    return answer


# 단독 실행: python fake_deploy.py <port> [bind] — 기본 = engine_answer(DATABASE_URL 필요), /_plan으로 동작 넣기
# bind 기본 127.0.0.1(host 시험). compose 시험 서비스(docker-compose.e2e.yml)만 0.0.0.0 — 같은 compose 망 안에서만 열림
if __name__ == "__main__":
    fake = FakeDeploy(port=int(sys.argv[1]), host=sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1")
    fake.default = engine_answer
    print(f"fake deploy {fake.url}", flush=True)
    fake.server.serve_forever()
