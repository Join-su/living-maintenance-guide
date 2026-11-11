"""Deployment Server FastAPI 앱(API 계약 docs/api-contract.md, 로컬 문서).

시작: Pack 버전을 git에서 읽기 전용 사본으로 꺼냄 → 실행 설정 작성(PostgreSQL backend면 실행용 DSN 하나만 프로세스 환경에) → packrt Runtime 불러오기.
요청: Idempotency-Key 확인 → 입력 schema 검사 → run_id·trace_id → packrt Runtime.run(공유 기록기 sink, 보고서 콜백) → 응답 저장.
import 경계(D-13): packrt·tracewriter만 가져다 쓰고 llmops는 import하지 않는다(tests/test_boundary.py)."""
import json
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from packrt.ir.releases import find_releases
from packrt.run.engine import Runtime
import tracewriter
from tracewriter import TraceWriter, TraceWriteError, new_run_id, new_trace_id, redact, redact_text

from . import __version__
from . import feedback
from .errors import TRACE_RETRYABLE, TRACE_STATUS, ApiError
from .execution import RUNTIME_DSN_ENV, dsn_secrets, runtime_dsn, scrub_db_env
from .registration import ledger_dsn, registration_check
from .ids import new_feedback_id, now_iso
from .pack_source import materialize, write_execution_config
from .runner import PackRunner, model_nodes
from .settings import load_settings
from .stores import IdempotencyStore, ReportStore, RunStore, idem_scope, request_sha256

KEY_RE = re.compile(r"^[\x21-\x7e]{1,255}$")
WHERE_RE = re.compile(r"— (\$[^:]*):")
RUN_PURPOSES = ("trial",)   # D-476 덧 9: X-Run-Purpose 값 목록(정정 확인 시험 Run) — trace run_started 본문 meta.purpose


class _PurposeSink:
    """X-Run-Purpose가 온 실행만: run_started 본문에 meta.purpose를 더해 넘긴다(packrt · tracewriter · trace.run 손 0). 나머지는 그대로."""

    def __init__(self, sink, purpose):
        self._sink, self._purpose = sink, purpose

    def emit(self, event):
        if isinstance(event, dict) and event.get("type") == "run_started" and isinstance(event.get("body"), dict):
            event = {**event, "body": {**event["body"], "meta": {"purpose": self._purpose}}}
        return self._sink.emit(event)

    def __getattr__(self, name):
        return getattr(self._sink, name)
CLOSE_WAIT_SECONDS = 120   # 종료 때 진행 중 실행을 기다리는 상한(그 뒤에는 기다리지 않고 닫음)
LIST_LIMIT, LIST_LIMIT_MAX = 20, 100   # GET /runs 한 쪽(지시 040 C-1)
log = logging.getLogger("deploy_server")


class Deployment:
    """배포 하나 = 서버 하나: Pack 버전 하나와 실행 설정 하나를 불러와 요청을 처리한다."""

    def __init__(self, settings, *, trace_writer=None, provider=None):
        """trace_writer: 공유 기록기(sink_for(ctx)를 가진 것). 없으면 설정 trace·registration으로 TraceWriter를 만든다(시험은 가짜를 넘김)."""
        self.settings = s = settings
        s.data_dir.mkdir(parents=True, exist_ok=True)
        self.copy = materialize(s.pack_repo, s.pack_ref, s.packs_dir, s.pack_subdir, s.releases_subdir)
        for root in s.release_roots_extra:   # R12 R-2: 둘째 찾는 곳은 packrt가 폴더를 요구함(R12가 만든 version이 들어갈 쓰기 폴더)
            root.mkdir(parents=True, exist_ok=True)
        cfg = write_execution_config(s.data_dir / "execution-config.yaml", self.copy, s.execution, s.release_roots_extra)
        # DB 접속 정보: 프로세스 환경에는 실행용 DSN(PostgreSQL 실행 설정일 때만)과 trace 기록기 이름만 남긴다(불러온 코드가 같은 프로세스에서 돎)
        self._runtime_dsn = runtime_dsn(s.runtime_db.get("env_file")) if s.uses_postgres else None
        self._ledger_dsn = ledger_dsn(s)   # registration llmops_pg: 장부도 runtime_reader로 읽음(같은 이름, 프로세스 환경을 지우기 전에 읽음)
        scrub_db_env(keep={s.trace["dsn_env"]} | ({RUNTIME_DSN_ENV} if s.uses_postgres else set()))
        env = None if s.provider != "fake" else {}
        if self._runtime_dsn:
            os.environ[RUNTIME_DSN_ENV] = self._runtime_dsn   # packrt가 store 접속을 찾는 곳(값은 출력·기록하지 않음)
            if env is not None:
                env[RUNTIME_DSN_ENV] = self._runtime_dsn
        if provider is None and s.provider == "fake":
            from .fake_provider import FakeProvider, load_rule
            provider = FakeProvider(rule=load_rule(s.pack_repo, self.copy.commit, s.packs_dir))   # 배포 트리의 규칙 모듈, 없으면 옛 규칙(ES-F)
        try:
            rt = Runtime(self.copy.pack_dir, provider=provider, env=env, config_name=str(cfg))
        except Exception as e:  # noqa: BLE001 - packrt 오류 문구에 접속 정보가 섞여도 밖으로 내지 않는다
            raise RuntimeError(f"Pack을 불러올 수 없습니다: {type(e).__name__}: {redact_text(str(e), self.secrets())}") from None
        self._embed_key = None
        if s.embedding:   # D-403 덧: 질의 임베딩만 실제 — 키는 packrt가 파일에서 메모리로만(배포단은 경로만 넘김), 주소는 설정
            self._attach_embedder(rt)
        if s.expect_revision and rt.revision != s.expect_revision:
            raise RuntimeError(f"Pack revision이 설정과 다릅니다: 설정 {s.expect_revision} / 불러온 사본 {rt.revision} ({self.copy.commit[:12]})")
        from packrt.trace import release_line
        line = release_line(rt)
        self.model_release_id = line.get("model_release_id")   # 장부 모델 칸 대조 기대값(D-326 Q-B, 모델 없는 Pack = None)
        self.running_models = line.get("models") or {}           # 모델 다름 거부 글(D-373): 이름 → release
        self.model_nodes = model_nodes(rt.doc)
        self._check = None
        self.runner = PackRunner(rt, review_policy=s.review_policy, outcome_by_node=s.outcome_by_node,
                                 refusal=lambda: getattr(self._check, "last_refusal", None))
        self.provider = rt.provider
        if trace_writer is None:
            t = s.trace
            trace_writer = TraceWriter(dsn_env=t["dsn_env"], env_file=t["env_file"], payload_root=t["payload_root"],
                                       secrets=self.secrets, registration_check=self._registration_check())
        self.trace = trace_writer
        self.boot_id = uuid.uuid4().hex   # 서버 시작마다 새 실행 키 범위(D-296)
        self.idem = IdempotencyStore(s.data_dir / "idempotency", scope=idem_scope(s.deployment_id, rt.revision, self.boot_id))
        self.feedback_idem = IdempotencyStore(s.data_dir / "feedback-idempotency")   # 피드백 키는 실행 키와 따로(지시 041)
        self.runs = RunStore(s.data_dir / "runs")
        self.reports = ReportStore(s.data_dir)
        self.lock = threading.Lock()   # 운영 1단계: 실행은 한 번에 하나(Runtime 자원 캐시·provider를 요청끼리 나누지 않음)
        self.started_at = now_iso()
        self._close_interrupted()
        for path, _rec in self.feedback_idem.running():   # 꺼질 때 쓰던 피드백 키는 같은 키로 다시 받게(retry)
            self.feedback_idem.reopen(path)

    def close(self):
        """서버 종료 때 기록기 연결을 닫는다(tracewriter 0.1.1 close, EV-002 CHG-02). 실행 lock을 먼저 잡으므로 진행 중 실행은
        run_ended까지 기록된 뒤에 닫힌다. close가 없는 기록기는 그대로 둔다. 오류는 종료를 막지 않는다(형 이름만 남김)."""
        held = self.lock.acquire(timeout=CLOSE_WAIT_SECONDS)
        try:
            fn = getattr(self.trace, "close", None)
            if fn is not None:
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - shutdown must finish
                    log.warning("기록기 close 실패: %s", type(e).__name__)
        finally:
            if held:
                self.lock.release()
            else:
                log.warning("진행 중 실행이 %s초 안에 끝나지 않아 기다리지 않고 닫았습니다", CLOSE_WAIT_SECONDS)

    def _registration_check(self):
        self._check = registration_check(self.settings.registration, self._ledger_dsn, self.model_release_id,
                                         self.running_models, self.model_nodes)   # llmops_db · llmops_pg · none(registration.py)
        return self._check

    def _attach_embedder(self, rt):
        from packrt.run.providers import HttpProvider
        p = ((rt.models or {}).get("providers") or {}).get("openai") or {}
        if not p.get("env") or not p.get("base_url_env"):
            raise RuntimeError("embedding 칸: Pack models.yaml에 openai provider의 env · base_url_env가 없습니다")
        if not hasattr(rt.provider, "embed") or not hasattr(type(rt.provider), "embedder"):
            raise RuntimeError("embedding 칸: Runtime provider가 배포단 가짜 provider가 아닙니다")
        if not Path(self.settings.embedding["env_file"]).is_file():   # 경로 · 값은 글에 넣지 않음
            raise RuntimeError("embedding 칸: env_file이 가리키는 키 파일이 없습니다")
        embedder = HttpProvider(rt.models, env={"PACKRT_ENV_FILE": str(self.settings.embedding["env_file"]), p["base_url_env"]: self.settings.embedding["base_url"]})
        if not embedder.env_file.get(p["env"]):   # 이름만 봄(값은 출력 · 기록 0) — 늦은 Run 실패 대신 시작 거절
            raise RuntimeError(f"embedding 칸: 키 파일에 {p['env']}가 없습니다")
        rt.provider.embedder = embedder
        self._embed_key = p["env"]

    def secrets(self):
        """가림에 쓸 비밀 값(환경 변수 값). 요청마다 다시 읽는다. 값은 가림에만 쓰고 어디에도 적지 않는다."""
        out = [os.environ[n] for n in self.settings.secret_env_names if os.environ.get(n)]
        embedder = getattr(getattr(getattr(self, "runner", None), "rt", None), "provider", None)
        embedder = getattr(embedder, "embedder", None)
        if embedder is not None and getattr(self, "_embed_key", None) and embedder.env_file.get(self._embed_key):
            out.append(embedder.env_file[self._embed_key])   # D-403 덧: 그 키 이름의 값만(메모리, 파일 다시 안 엶)
        for dsn in {self._runtime_dsn, self._ledger_dsn} - {None}:
            out += dsn_secrets(dsn)
        return out

    # ------------------------------------------------------------------ 공통 칸
    def pack_info(self):
        return {"pack_id": self.runner.pack_id, "pack_revision": self.runner.rt.revision, "commit": self.copy.commit, "tag": self.copy.tag}

    def provider_info(self):
        """모델 모드(지시 040 C-2 · C-4): fake면 배포 트리 규칙 이름도. 실제 호출 여부는 설정 provider가 정한다."""
        out = {"mode": self.settings.provider}
        if self.settings.embedding:
            out["embedding"] = "http"   # D-403 덧: 질의 임베딩만 실제(값 · 경로 0)
        if self.settings.provider == "fake":
            out["fake_rule"] = getattr(self.provider, "rule_name", None)
        return out

    def info(self):
        return {"deployment_id": self.settings.deployment_id, "pack": self.pack_info(), "data_release_id": self.runner.data_release_id,
                "runtime_version": self.runner.runtime_version, "provider": self.provider_info(), "review_policy": self.settings.review_policy,
                "server": {"name": "llmopsagent-deploy", "version": __version__, "started_at": self.started_at}, "releases": self.releases_info()}

    def releases_info(self):
        """갈래 k KS-4(약속 E · D-265): 실행 설정이 고른 store · model release 이름 — 설정에서 읽기만. drs · 등록 대조는 그대로."""
        ex = self.settings.execution or {}
        pick = lambda group: {k: v.get("release") for k, v in sorted((ex.get(group) or {}).items()) if isinstance(v, dict)}
        return {"stores": pick("stores"), "models": pick("models")}

    def health(self):
        return {"status": "ok", "deployment_id": self.settings.deployment_id, "pack_revision": self.runner.rt.revision}

    def _summary(self, run_id, trace_id, started_at):
        prov = self.provider_info()
        if prov["mode"] == "fake":
            prov["fake_limits"] = None   # 실행이 끝날 때 채움(C-4)
        return {"run_id": run_id, "status": "running", "outcome": None, "deployment_id": self.settings.deployment_id, "pack": self.pack_info(),
                "data_release_id": self.runner.data_release_id, "runtime_version": self.runner.runtime_version,
                "trace": {"contract": 2, "trace_id": trace_id, "run_id": run_id}, "report": None, "review": None,
                "started_at": started_at, "ended_at": None, "href": f"/runs/{run_id}", "provider": prov}

    def _limits_mark(self):
        return getattr(self.provider, "count", None)

    def _limits_since(self, mark):
        """이 실행의 가짜 한계(중복 없이, 처음 나온 차례). 세지 못하는 provider면 None."""
        if mark is None or not hasattr(self.provider, "limits_since"):
            return None
        got, truncated = self.provider.limits_since(mark)
        out = {"fake_limits": list(dict.fromkeys(r for limits in got for r in limits))}
        if truncated:
            out["fake_limits_truncated"] = True
        return out

    # ------------------------------------------------------------------ POST /runs
    def post_run(self, body, key, traceparent=None, purpose=None):
        """돌려줌: (HTTP 상태, 본문, 재요청 여부). purpose = X-Run-Purpose(없으면 None — 지금과 같음)."""
        if key is None or key == "":
            raise ApiError(400, "idempotency_key_missing", "Idempotency-Key 헤더가 필요합니다", where="header:Idempotency-Key")
        if not KEY_RE.match(key):
            raise ApiError(400, "idempotency_key_invalid", "Idempotency-Key는 공백 없는 ASCII 1~255자입니다", where="header:Idempotency-Key")
        if purpose is not None and purpose not in RUN_PURPOSES:
            raise ApiError(400, "run_purpose_invalid", f"X-Run-Purpose는 {', '.join(RUN_PURPOSES)} 중 하나입니다", where="header:X-Run-Purpose")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(400, "invalid_json", f"본문이 JSON이 아닙니다: {e}", where="$")
        req_sha = request_sha256(value) if purpose is None else request_sha256({"body": value, "purpose": purpose})   # 머리글도 요청의 일부
        rec = self.idem.lookup(key, req_sha)
        if rec is not None:
            return rec["response"]["status"], rec["response"]["body"], True
        try:
            self.runner.check_input(value)
        except ValueError as e:
            m = WHERE_RE.search(str(e))
            raise ApiError(422, "invalid_input", str(e), where=m.group(1) if m else "$")
        run_id, trace_id = new_run_id(), new_trace_id(traceparent)
        if not self.idem.claim(key, req_sha, run_id):   # 같은 키가 동시에 왔다
            rec = self.idem.lookup(key, req_sha)
            return rec["response"]["status"], rec["response"]["body"], True
        summary = self._summary(run_id, trace_id, now_iso())
        self.runs.save({**summary, "result": None, "error": None})
        fake = None
        try:
            with self.lock:
                mark = self._limits_mark()
                try:
                    res = self._execute(value, run_id, trace_id, purpose)
                finally:
                    fake = self._limits_since(mark)
        except Exception as e:  # noqa: BLE001 - every claimed key must end with a stored response
            res = {"status": "failed", "outcome": None, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {str(e)[:300]}",
                   "where": None, "retryable": False, "details": {}, "kind": "internal"}, "artifact": None, "review": None, "output": None, "state": None}
        if fake is not None and summary["provider"]["mode"] == "fake":
            summary = {**summary, "provider": {**summary["provider"], **fake}}
        status, payload, record = self._finish(summary, res)
        self.runs.save(record)
        self.idem.complete(key, status, payload)
        return status, payload, False

    def _execute(self, value, run_id, trace_id, purpose=None):
        s = self.settings
        ctx = {"run_id": run_id, "trace_id": trace_id, "origin": "deployment", "evaluation_run_id": None,
               "deployment_id": s.deployment_id, "retention_class": s.retention_class}
        sink = self.trace.sink_for(ctx)
        if purpose is not None:
            sink = _PurposeSink(sink, purpose)

        def on_artifact(node_path, fmt, text, review=None):   # 보고서 파일도 공용 가림 함수를 거친다(015a)
            return self.reports.write(run_id, node_path, fmt, redact_text(text, self.secrets()), review=review, provider=self.provider_info())
        return self.runner.run(value, context=ctx, sink=sink, on_artifact=on_artifact)

    def _finish(self, summary, res):
        summary = {**summary, "status": res["status"], "outcome": res.get("outcome"), "ended_at": now_iso()}
        if res["status"] == "succeeded":
            art = res.get("artifact")
            summary["report"] = ({"report_id": art["report_id"], "format": art["format"], "sha256": art["sha256"], "href": f"/reports/{art['report_id']}"}
                                 if art else None)
            summary["review"] = {k: v for k, v in (res.get("review") or {}).items() if k in ("actor_kind", "action", "note")} or None
            state = res.get("state") or {}
            result = {"output": res.get("output"), "grounding": {k: state.get(k) for k in self.settings.grounding_keys if k in state}}
            result = redact(result, secrets=self.secrets())[0]   # 응답·실행 요약에도 비밀 값이 남지 않게(보고서·trace와 같은 가림)
            return 201, {**summary, "result": result}, {**summary, "result": result, "error": None}   # 201에도 결과(갈래 k KS-1, D-259 (4)) — 목록 항목은 그대로 뺌
        err = res["error"]
        code = err["code"]
        status = 500
        if err.get("kind") == "trace":
            status = TRACE_STATUS.get(code, 500)
        e = ApiError(status, code, err["message"], where=err.get("where"), retryable=err.get("retryable", code in TRACE_RETRYABLE),
                     run_id=summary["run_id"], details=err.get("details"))
        return status, e.body(), {**summary, "result": None, "error": e.body()["error"]}

    def _close_interrupted(self):
        """다시 켤 때: running으로 남은 키를 run_interrupted로 닫는다(API 계약 3절). 실행을 이어서 하지 않는다."""
        for path, rec in self.idem.running():
            e = ApiError(500, "run_interrupted", "실행 중에 서버가 꺼졌습니다. 새 키로 다시 요청하세요", run_id=rec["run_id"])
            run = self.runs.get(rec["run_id"]) or {"run_id": rec["run_id"], "href": f"/runs/{rec['run_id']}"}
            self.runs.save({**run, "status": "failed", "ended_at": now_iso(), "result": None, "error": e.body()["error"]})
            self.idem.close_interrupted(path, 500, e.body())

    # ------------------------------------------------------------------ POST /runs/{run_id}/feedback
    def post_feedback(self, run_id, body, key):
        """사람 피드백 하나(지시 041 C-1, D-182): 실행은 돌리지 않고 tracewriter record_feedback으로 trace.feedback 한 줄.
        돌려줌: (HTTP 상태, 본문, 재요청 여부). 실행 lock은 잡지 않는다(긴 실행 중에도 받음)."""
        if key is None or key == "":
            raise ApiError(400, "idempotency_key_missing", "Idempotency-Key 헤더가 필요합니다", where="header:Idempotency-Key")
        if not KEY_RE.match(key):
            raise ApiError(400, "idempotency_key_invalid", "Idempotency-Key는 공백 없는 ASCII 1~255자입니다", where="header:Idempotency-Key")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(400, "invalid_json", f"본문이 JSON이 아닙니다: {e}", where="$")
        req_sha = request_sha256({"run_id": run_id, "body": value})   # 같은 키 · 같은 본문을 다른 실행에 보내면 reused
        rec = self.feedback_idem.lookup(key, req_sha)
        if rec is not None and rec["state"] == "done":
            return rec["response"]["status"], rec["response"]["body"], True
        run = self.runs.get(run_id)
        if run is None:
            raise ApiError(404, "run_not_found", f"실행이 없습니다: {run_id}", where="path:run_id")
        if run.get("status") != "succeeded":
            raise ApiError(409, "run_not_succeeded", f"성공한 실행에만 피드백을 남깁니다(지금 {run.get('status')})", where="path:run_id",
                           run_id=run_id)
        if feedback.is_check_record(value):
            return self._post_check_record(run_id, value, key, req_sha, rec)
        review_node = self._review_node(run)
        allowed, editable = feedback.review_context(self.runner.rt, review_node)
        secrets = self.secrets()
        problems = feedback.check_body(value, allowed=allowed, editable=editable, nodes=feedback.node_ids(self.runner.rt), secrets=secrets)
        if rec is not None:
            fid, at = rec["feedback_id"], rec["at"]
        else:
            fid, at = new_feedback_id(), now_iso()
        args = {"feedback_id": fid, "run_id": run_id, "at": at, "action": value.get("action"), "actor": value.get("actor"),
                "reason": value.get("reason"), "retention_class": self.settings.retention_class, "label": value.get("label"),
                "review_node": review_node, "node": value.get("node"), "edited_value": value.get("edits")} if not problems else None
        shared = getattr(tracewriter, "check_feedback", None)   # 0.1.3~: 같은 규칙을 한 번 더(비밀 값 목록 넘김)
        if not problems and shared is not None:
            problems = feedback.check_shared(shared, {**args, "source": "deploy_api"}, secrets)
        if problems:
            where, message = problems[0]
            raise ApiError(422, "invalid_input", message, where=where, run_id=run_id,
                           details={"problems": [{"where": w, "message": m} for w, m in problems]})
        if rec is not None:
            self.feedback_idem.reclaim(key)
        elif not self.feedback_idem.claim(key, req_sha, run_id, extra={"feedback_id": fid, "at": at}):   # 같은 키가 동시에 왔다
            rec = self.feedback_idem.lookup(key, req_sha)
            if rec is not None and rec["state"] == "done":
                return rec["response"]["status"], rec["response"]["body"], True
            raise ApiError(409, "idempotency_in_progress", "같은 Idempotency-Key의 앞 요청이 아직 처리 중입니다. 잠시 뒤 같은 키로 다시 보내세요",
                           where="header:Idempotency-Key", retryable=True, run_id=run_id)
        status, payload = self._write_feedback(key, args)
        self.feedback_idem.complete(key, status, payload)
        return status, payload, False

    def _post_check_record(self, run_id, value, key, req_sha, rec):
        """판 2(D-412): 사람 체크 기록 문서 한 장 → 기록기 record_check_record → trace.feedback 한 줄(kind check_record).
        실행은 돌리지 않는다. 문서 겉만 보고(feedback.check_record_body) 뜻 · 신호 셈은 보지 않는다. 같은 키 규칙 = 판 1."""
        secrets = self.secrets()
        problems, meta = feedback.check_record_body(value, run_id=run_id, key=key, deployment_id=self.settings.deployment_id, secrets=secrets)
        if problems:
            where, message = problems[0]
            raise ApiError(422, "invalid_input", message, where=where, run_id=run_id,
                           details={"problems": [{"where": w, "message": m} for w, m in problems]})
        fid, at = (rec["feedback_id"], rec["at"]) if rec is not None else (new_feedback_id(), now_iso())
        args = {"feedback_id": fid, "run_id": run_id, "at": at, "actor": feedback.CHECK_ACTOR,
                "retention_class": self.settings.retention_class, **meta, "record": value["record"]}
        if rec is not None:
            self.feedback_idem.reclaim(key)
        elif not self.feedback_idem.claim(key, req_sha, run_id, extra={"feedback_id": fid, "at": at}):
            rec = self.feedback_idem.lookup(key, req_sha)
            if rec is not None and rec["state"] == "done":
                return rec["response"]["status"], rec["response"]["body"], True
            raise ApiError(409, "idempotency_in_progress", "같은 Idempotency-Key의 앞 요청이 아직 처리 중입니다. 잠시 뒤 같은 키로 다시 보내세요",
                           where="header:Idempotency-Key", retryable=True, run_id=run_id)
        wrote = []

        def ok(got):
            wrote.append(got.get("result"))
            return {"feedback_id": got.get("feedback_id") or fid, "run_id": run_id, "at": got.get("at") or at,
                    "kind": feedback.CHECK_KIND, "record_key": meta["record_key"],
                    "record_ref": got.get("record_ref")}   # duplicate면 기록기가 저장된 id · 시각을 돌려줌(packrt O-2)
        status, payload = self._write_feedback(key, args, writer="record_check_record", ok=ok)
        self.feedback_idem.complete(key, status, payload)
        if status == 201 and wrote and wrote[0] != "duplicate" and self.settings.field_gold:
            self._field_gold_job(run_id, value["record"], meta["record_key"])
        return status, payload, False

    def _field_gold_job(self, run_id, record, record_key):
        """D-470 덧 5(FIELD-GOLD-E1): 새로 쓴 체크 기록 하나 → 일감 파일 <data_dir>/field-gold/outbox/<record_key>.json(원자 쓰기).
        현장 정답 CLI는 일꾼(scripts/field-gold.py)이 부름. 여기서 실패해도 응답은 그대로(체크 기록은 이미 trace에 있음)."""
        try:
            start = (record.get("runs") or {}).get("start")
            start_rec = self.runs.get(start) if isinstance(start, str) else None
            found = find_releases([self.copy.releases_dir, *self.settings.release_roots_extra])   # 실행기와 같은 찾기 규칙(manifest 한두 단)
            stores = {}
            for name, b in self.runner.rt.bindings["stores"].items():
                rid = b.get("release_id")
                places = found.get(rid) or {}
                stores[name] = {"release_id": rid, "dir": str(next(iter(places.values()))) if len(places) == 1 else None}
            job = {"record_key": record_key, "run_id": run_id, "start_run_id": start, "record": redact(record, secrets=self.secrets())[0],
                   "data_release_id": self.runner.data_release_id,
                   "release_match": bool(start_rec) and start_rec.get("data_release_id") == self.runner.data_release_id,
                   "stores": stores, "received_at": now_iso()}
            d = self.settings.data_dir / "field-gold" / "outbox"
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{record_key}.json.tmp"
            tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, d / f"{record_key}.json")
        except Exception as e:  # noqa: BLE001 - 일감 실패가 체크 기록 응답을 바꾸지 않음
            log.warning("현장 정답 일감 못 씀(%s): %s", record_key, type(e).__name__)

    def _review_node(self, run):
        """그 실행이 지난 검토 node: 보고서 meta의 review.node_path 끝 이름(실행 요약에는 칸을 더하지 않음). 없으면 None."""
        rep = self.reports.get(run["report"]["report_id"]) if run.get("report") else None
        path = ((rep or {}).get("review") or {}).get("node_path")
        return path.rsplit(".", 1)[-1] if path else None

    def _write_feedback(self, key, args, writer="record_feedback", ok=None):
        """기록기 부르기 → (상태, 본문). trace_unavailable만 저장하지 않고 503(같은 키로 다시 → 같은 id · 시각).
        writer = 판 1 record_feedback · 판 2 record_check_record(D-412) — 기록기에 없으면 501(지금 배포 영향 0)."""
        write = getattr(self.trace, writer, None)
        if write is None:
            what = "체크 기록(판 2)" if writer == "record_check_record" else "피드백 기록(0.1.3부터)"
            e = ApiError(501, "feedback_unsupported", f"이 서버의 기록기(tracewriter)는 {what}을 모릅니다", where="trace", run_id=args["run_id"])
            return 501, e.body()
        try:
            got = write(**args)
        except TraceWriteError as e:
            if e.code == "trace_unavailable":
                self.feedback_idem.retry(key)
                raise ApiError(503, e.code, redact_text(e.message, self.secrets()), where="trace", retryable=True, run_id=args["run_id"])
            return 500, ApiError(500, e.code, redact_text(e.message, self.secrets()), where="trace", run_id=args["run_id"]).body()
        except Exception as e:  # noqa: BLE001 - every claimed key must end with a stored response
            return 500, ApiError(500, "internal_error", type(e).__name__, run_id=args["run_id"]).body()
        if ok is not None:
            return 201, ok(got or {})
        return 201, {"feedback_id": args["feedback_id"], "run_id": args["run_id"], "at": args["at"], "edited_value_ref": got.get("edited_value_ref")}

    # ------------------------------------------------------------------ GET
    def get_run(self, run_id):
        rec = self.runs.get(run_id)
        if rec is None:
            raise ApiError(404, "run_not_found", f"실행이 없습니다: {run_id}", where="path:run_id")
        return rec

    def list_runs(self, limit=None, before=None):
        """GET /runs(지시 040 C-1): 최신 순 한 쪽. 질의가 틀리면 422 invalid_query."""
        if limit is None:
            n = LIST_LIMIT
        else:
            try:
                n = int(limit)
            except ValueError:
                n = 0
            if not 1 <= n <= LIST_LIMIT_MAX or not limit.strip().isdigit():
                raise ApiError(422, "invalid_query", f"limit은 1~{LIST_LIMIT_MAX} 정수입니다", where="query:limit")
        if before is not None and (not before.startswith("run_") or not before[4:].isalnum()):
            raise ApiError(422, "invalid_query", "before는 run_id(run_ + 영숫자)입니다", where="query:before")
        items, nxt = self.runs.list(n, before)
        return {"items": items, "next_before": nxt}

    def get_report(self, report_id):
        rec = self.reports.get(report_id)
        if rec is None:
            raise ApiError(404, "report_not_found", f"보고서가 없습니다: {report_id}", where="path:report_id")
        return rec


def create_app(settings=None, *, deployment=None):
    dep = deployment or Deployment(settings)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await run_in_threadpool(dep.close)   # uvicorn은 진행 중 요청을 마친 뒤 여기로 옴

    # 생성 문서(/docs · /redoc · /openapi.json)는 끔 — 본문을 직접 읽어 입력 · 응답 schema가 비어 보이고, 정본은 API 계약 문서(지시 040 C-5)
    app = FastAPI(title="LLMOpsAgent Deployment Server", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deployment = dep

    @app.exception_handler(ApiError)
    async def api_error(_request, exc):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(Exception)
    async def internal_error(_request, exc):
        return JSONResponse(ApiError(500, "internal_error", f"{type(exc).__name__}").body(), status_code=500)

    @app.post("/runs")
    async def post_runs(request: Request):
        body = await request.body()
        status, payload, replayed = await run_in_threadpool(dep.post_run, body, request.headers.get("idempotency-key"), request.headers.get("traceparent"),
                                                           request.headers.get("x-run-purpose"))
        headers = {"Idempotent-Replayed": "true"} if replayed else {}
        if status == 201:
            headers["Location"] = payload["href"]
        return JSONResponse(payload, status_code=status, headers=headers)

    @app.post("/runs/{run_id}/feedback")
    async def post_feedback(run_id: str, request: Request):
        body = await request.body()
        status, payload, replayed = await run_in_threadpool(dep.post_feedback, run_id, body, request.headers.get("idempotency-key"))
        return JSONResponse(payload, status_code=status, headers={"Idempotent-Replayed": "true"} if replayed else {})

    @app.get("/runs")
    def list_runs(request: Request):
        q = request.query_params
        return dep.list_runs(q.get("limit"), q.get("before"))

    @app.get("/info")
    def info():
        return dep.info()

    @app.get("/health")
    def health():
        return dep.health()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        return dep.get_run(run_id)

    @app.get("/reports/{report_id}")
    def get_report(report_id: str):
        return dep.get_report(report_id)

    return app


def create_app_from_env():
    """uvicorn --factory deploy_server.app:create_app_from_env (설정 파일 경로: DEPLOY_CONFIG)."""
    return create_app(load_settings(Path(os.environ.get("DEPLOY_CONFIG", "config/dev-local.yaml"))))
