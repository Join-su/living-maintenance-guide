"""FastAPI skeleton: static UI, mode display. Each later boundary registers its own routes on this app."""
import base64
import contextlib
import gzip
import json
import urllib.request
import os
import re
import shutil
import sys
from pathlib import Path

import asyncio
import threading
import time
import tempfile
import types
import uuid

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from packrt.ir.releases import dir_hash
from starlette.concurrency import run_in_threadpool

from . import autodeviate, autoreview, compare, deploying, editing, evaluation, graphread, guard, node_detail, opsruns, pack_ci, packdiff, packs, picker, suites, versions
from .compare import suite_tag
from . import gateway as gatewaymod
from .gateway import FakeFault, FakeProvider, Gateway, NoKey
from .keys import MODEL_RE, PROVIDERS, STATUS_LABEL, Keys, agent_llm, project_llm
from .runs import Executor
from .config import ADDRESS_NOTICE, REPO_ROOT, Settings, load_settings
from .parsing import ParseError, parse_file
from .store import DB_ERRORS, INTEGRITY_ERRORS, StoreStartError, StoreUnavailable, open_store
from . import aisuggest, autocand, autocycle, autodiag, autohyp, automain, autosealed, fixedflow, graphrel, kgcand, kgdiff, kglink, kgsignals
from .store import suite_key


class Scoring(BaseModel):
    skip: bool = False
    rule: bool = True
    llm: bool = True
    llm_provider: str | None = None   # EV-005 CHG-27: only kept when LLM scoring is on
    llm_model: str | None = None


class Rule(BaseModel):
    pass_: float = Field(0.80, alias="pass", ge=0, le=1)
    below: str = Field("hold", pattern="^(hold|reject)$")


class FileUpload(BaseModel):
    name: str
    text: str


class NewProject(BaseModel):
    name: str = Field(min_length=1)
    desc: str = ""
    scoring: Scoring = Scoring()
    rule: Rule = Rule()
    data: FileUpload | None = None

class ReleaseSource(BaseModel):
    how: str = Field(pattern="^(local|upload|builtin)$")
    path: str = ""
    files: dict[str, str] | None = None   # relative path -> base64 content (upload)
    builtin: str = ""                     # "<example dir>/<release dir>"
    config: str = ""                      # Agent Pack only: execution config file (EV-002 CHG-04)


class NewRelease(ReleaseSource):
    name: str = Field(min_length=1)


class RunInput(BaseModel):
    kind: str = Field(pattern="^(data|set|suite)$")
    id: str = ""


class NewSuite(BaseModel):
    release: str
    suite: str = Field(min_length=1)


class NewPromotion(BaseModel):   # EV-025 OPS-3 CHG-125
    by: str = ""
    reason: str = ""


AUTO_OFF = "자동 진단 꺼짐 — 저장 표 없음(migration 승인 대기)"   # EV-069: the auto tables are missing


class Confirm(BaseModel):   # EV-069 CHG-EF-1: a person confirms a version's fixed flow
    by: str = ""


class StopWheel(BaseModel):   # D-476 덧 20: '이 바퀴 멈춤'
    by: str = ""
    reason: str = ""


class QualityHuman(BaseModel):   # D-652 다섯째: '사람 확인 — 이 줄 통과 / 버림'
    decision: str = ""
    by: str = ""
    reason: str = ""


class StartEfficiency(BaseModel):   # EV-069 CHG-EF-2: '효율 개선 시작'
    by: str = ""
    goals: list[str] = Field(default_factory=lambda: list(autocycle.GOAL_ORDER))
    work_suite: str | None = None
    confirm_suite: str | None = None


class AutoSettings(BaseModel):   # EV-069 CHG-EF-2 (EV-030 CHG-159 C1, the efficiency row only)
    by: str = ""
    efficiency: dict = Field(default_factory=dict)


class NewLabel(BaseModel):   # EV-030 CHG-154: a person's label on an operational Run (append only)
    label: str = ""
    reason: str = ""
    by: str = ""
    node: str | None = None


class PromotedSuite(BaseModel):   # EV-025 OPS-3 CHG-126
    suite: str
    promotions: list[int] = Field(min_length=1)


class NewInputSet(BaseModel):
    how: str = Field(pattern="^(file|direct)$")
    name: str = ""
    text: str = ""


class GenerateInputs(BaseModel):
    release: str
    count: int = Field(5, ge=1, le=20)
    desc: str = ""


class NewVerdict(BaseModel):
    v: str = Field(pattern="^(adopt|hold|reject)$")
    reason: str = ""
    by: str = ""   # EV-022 answer 019-A E-S-03 Q1: the decider's alias id (optional; no name or contact)
    excl: str = ""
    hyp: str = ""


class NewRun(BaseModel):
    release: str
    input: RunInput


class Brief(BaseModel):
    title: str = ""
    fields: dict[str, str] = {}


class NewBundle(BaseModel):
    releases: list[str]
    input: RunInput


def run_view(r):
    """The Run as the UI reads it (no key values: everything in it was redacted before it was stored). A Run of the removed
    candidate feature is an earlier-flow record (EV-022 S-15): its stored release name kept the old suffix, which is not shown."""
    rel_name = re.sub(r"\s·\s후보\s.*$", "", r["rel_name"]) if r.get("candidate") else r["rel_name"]   # only that suffix (review E-S-01-R2-02)
    return {"id": r["uid"], "label": r["label"], "rel": r["rel"], "relName": rel_name, "graph": r["graph"], "graphId": r["graphId"],
            "code": r["code"], "input": r["input"], "status": r["status"], "mode": r["mode_label"], "modeKey": r["mode"], "limit": r["limit"],
            "llmKey": r["llmKey"], "at": time.strftime("%m-%d %H:%M", time.localtime(r["created_at"])), "notice": r.get("notice"),
            "cases": [{**c, "inputSha": "sha256:" + automain.input_sha(c.get("input"))} for c in r["cases"]],   # D-432 덧 7: the case list's mouse-over
            "summary": r.get("summary"), "verdicts": r.get("verdicts", []), "manifest": r.get("manifest"), "judge": r.get("judge"), "auto": r.get("auto"),
            "legacy": bool(r.get("candidate"))}


HOW_LABEL = {"local": "로컬 경로", "upload": "업로드", "builtin": "내장 예시"}
DEV_NONE = "같은 revision의 Pack 상태 파일이 없어 배포 승인할 수 없습니다 — Pack 쪽 개발자 변경(D-164)이 먼저 태그되어야 합니다"
DEV_MANY = "같은 revision의 Pack 상태 파일이 둘 이상입니다({ids}) — 고르지 않습니다"
DEV_UNTAGGED = "Pack 쪽 개발자 변경({id})이 아직 태그 전입니다({stage})"
DEV_CHANGE_LOCK = threading.Lock()   # EV-037 CHG-191: one deploy approval per Pack change id at a time (the server process)
CONFIRM_LABEL = "편집 확정"   # EV-022 CHG-109: a release made by confirming an edit (its folder is in the LLMOps data folder)
VERDICT_WORD = {"adopt": "채택", "hold": "보류", "reject": "버림"}   # D-470 덧 21: the verdict tab's words (EV-075 · v2 18쪽) — web/app.js the same
HYP_SOURCE_WORD = {"document": "시작 가설 문서", "agent": "가설 에이전트", "rule": "규칙", "harness": "시험 장치"}   # web/autocycle.js SRC the same
TARGET_WORD = {"node": "노드", "relation": "관계"}   # D-549 덧 3 감안 2: a diagnosis or hypothesis aims at a node, or (dev-2) a graph relation


def hyp_value(v):
    """A hypothesis value as the screen reads it (autocycle.js val): a float to two places, a dict · list as JSON."""
    if isinstance(v, (dict, list)):
        return graphrel.value_text(v) or json.dumps(v, ensure_ascii=False)   # dev-2 (D-546 덧 29): a relation value as one line (autocycle.js relVal)
    if isinstance(v, float) and round(v, 2) != v:
        return str(round(v, 2))
    return "—" if v is None else str(v)


def order_change(label, knob, v):
    """D-565 #7 (덧 3 · Pack 72d4414): check_first_<group> = s4_5 reads '점검 순서 CL · 4.5 먼저'; none reads '점검 순서 CL · 지금 순서'."""
    g, sec = str(knob or "")[len(autohyp.ORDER_PREFIX):].upper(), autohyp.order_section(v)
    return f"{label} {g} · {sec} 먼저" if sec else f"{label} {g} · 지금 순서" if v == autohyp.ORDER_NONE else f"{label} {g} · {hyp_value(v)}"


def hyp_cells(h):
    """D-549 덧 3 감안 4: one hypothesis in the general cells '대상 · 바꿈 · 근거' — target {kind, word, id}, the change in words (its
    kind's label from autohyp.HYP_KINDS, else the kind's own name) and the evidence (who made it · its reason). A later kind (dev-2's
    relation condition) or target kind reads in the same cells."""
    d = h.get("detail") or {}
    kind = autohyp.hyp_kind(h)
    tk = d.get("target_kind") or ("relation" if kind == "relation" else "node")
    label = (autohyp.HYP_KINDS.get(kind) or {}).get("label") or kind
    knob = str(h.get("knob") or "")
    if knob.startswith("구조:"):   # D-568 A3 1: the efficiency wheel's structure hypothesis ('구조:<shape>') — not an option
        label, knob = "구조", knob[len("구조:"):]
    why = d.get("why") if "why" not in h else h.get("why")
    src = HYP_SOURCE_WORD.get(h.get("source"), h.get("source") or "")
    rel = graphrel.value_text(h.get("value")) if kind == "relation" else None   # D-560 #6: 'EX-R-0001 좁힘 · … · 설비 M-01'
    return {"target": {"kind": tk, "word": TARGET_WORD.get(tk, tk), "id": d.get("target") or ((h.get("value") or {}).get("id") if rel else None) or h.get("node")},
            "change": f"{label} {rel}" if rel else order_change(label, knob, h.get("value")) if kind == "order" else f"{label} {knob} → {hyp_value(h.get('value'))}",
            "evidence": " · ".join(x for x in (src, why) if x) or "—"}


WEB_DIR = REPO_ROOT / "web"
EXAMPLES_DIR = REPO_ROOT / "examples"
PREVIEW_MAX_BYTES = 1_000_000   # patch preview body (EV-018 CHG-87)
PREVIEW_MAX_OPS = 100


def load_examples():
    out = []
    for meta in sorted(EXAMPLES_DIR.glob("*/example.json")):
        m = json.loads(meta.read_text())
        out.append({**m, "key": meta.parent.name})
    return out


def builtin_releases():
    return [{"id": f"{ex['key']}/{r['dir']}", "name": r["name"], "example": ex["name"]} for ex in load_examples() for r in ex["releases"]]


def strip_common_root(files):
    """An uploaded folder arrives as 'folder/a.py, folder/b.py'; read it from inside that folder."""
    parts = [Path(k).parts for k in files]
    if parts and all(len(p) > 1 for p in parts) and len({p[0] for p in parts}) == 1:
        return {str(Path(*Path(k).parts[1:])): v for k, v in files.items()}
    return files


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        # EV-030 CHG-151: the automatic diagnosis runs while the server is up (product settings; tests ask for it), never without
        # the auto-cycle tables (V-DIAG-OFF). app.state.autodiag exists by the time the server starts.
        if settings.auto_diag:
            _app.state.autodiag.start()
        yield
        _app.state.autodiag.stop()

    app = FastAPI(title="LLMOps", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(StoreUnavailable)
    async def store_unavailable(request, exc):   # EV-057 CHG-239: the shared PostgreSQL connection was lost — 503 with its text only
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/mode")
    def mode():
        # display only: the mode is changed outside the product (LLMOPS_LLM_MODE), never through the API
        return {"mode": settings.mode, "label": settings.mode_label, "notice": ADDRESS_NOTICE}

    try:
        store = open_store(settings)   # EV-029 CHG-130: SQLite by default, the shared PostgreSQL llmops schema with LLMOPS_STORE=pg
    except StoreStartError as e:
        raise SystemExit(f"저장소를 시작할 수 없습니다 — {e.kind}: {e}") from None
    app.state.store = store
    keys = Keys(key_file=settings.openai_env_file)
    keys.add_dsn(packs.runtime_dsn(settings.db_env_file))   # the runtime DSN a PostgreSQL Pack may get is masked like a key everywhere (EV-020 CHG-97)
    # EV-VPS-01 CHG-V07 (VPS only): the runner requests; it reads and writes the same Pack status folder the window reads, and every
    # token value it reads is masked like a key wherever text is recorded
    ci = pack_ci.PackCI(settings.pack_ci, settings.data_dir, status_dir=settings.pack_status_dir, on_secret=keys.add_dsn) if settings.pack_ci else None
    app.state.pack_ci = ci
    if ci:
        try:
            ci.token()
        except pack_ci.CIError:
            pass
    fake = FakeProvider(faults=settings.fake_faults)
    app.state.keys, app.state.fake = keys, fake
    app.state.gateway = Gateway(settings, keys, fake)
    store.redact = lambda s: guard.redact(s, keys.known_secrets())  # every stored text is masked first (AC-26)

    def settings_view():
        status = keys.status()   # key / key_label: the OpenAI key, kept for compatibility (EV-005)
        providers = [{"id": pid, "name": p["name"], "title": p["title"], "status": keys.status(pid), "status_label": STATUS_LABEL[keys.status(pid)],
                      "env": list(p["env"]), "default_model": p["model"]} for pid, p in PROVIDERS.items()]
        return {"key": status, "key_label": STATUS_LABEL[status], "providers": providers, "mode": settings.mode, "mode_label": settings.mode_label,
                "notice": ADDRESS_NOTICE}

    def own_origin(request: Request, kind: str, message: str):
        """A request carrying an Origin is taken only from this server's own page: a page on another site cannot make the
        browser change keys or open the chooser. A request without an Origin (a local tool or test) is not a browser
        request from another site and passes; the product listens on 127.0.0.1 only (EV-003, EV-005)."""
        origin = request.headers.get("origin")
        if origin is not None and origin not in (f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"):
            raise HTTPException(status_code=403, detail={"kind": kind, "message": message})

    def json_request(request: Request, kind: str):
        if not request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json":
            raise HTTPException(status_code=422, detail={"kind": kind, "message": "JSON 요청만 받습니다"})

    writer = None
    if settings.trace == "postgres":   # EV-019 CHG-93: evaluation cases go to the shared trace too (week 1: registration checked in code)
        from tracewriter import TraceWriter
        writer = TraceWriter(dsn_env="LLMOPSAGENT_TRACE_DSN", env_file=settings.db_env_file, payload_root=settings.payload_root,
                             secrets=keys.known_secrets, registration_check=store.registration_check)   # EV-029 CHG-134: read through the Store
    app.state.trace_writer = writer
    executor = Executor(settings, store, keys, fake, gw=app.state.gateway, writer=writer)
    app.state.executor = executor
    # EV-035 ML-3: the wheel's rows run on the same executor code with their records kept in memory (a row's run is never a Run, Q-7 A);
    # no trace writer (U-2)
    shadow_store = autocand.ShadowStore(store)
    shadow_exec = Executor(settings, shadow_store, keys, fake, gw=app.state.gateway, writer=None)

    def sealed_run(rel, cases, budget=None):
        """D-477 (2) (보고 073 S-4): the sealed set's cases on one version, by the usual executor on a store in memory only (no Run row,
        no trace, no judge) — the cases as they ended. D-486 덧 9 · 12: `budget` (USD) cuts the Run after the case whose spend so far
        (autosealed.sealed_spend: its tokens × prices, the careful unit where none came back) passes it — the cases not run end '실행 안 됨'."""
        proj = {**(store.project(rel["project_id"]) or {}), "scoring": {"skip": True}}
        mem = autosealed.MemoryRuns(store, proj)
        ex = Executor(settings, mem, keys, fake, gw=app.state.gateway, writer=None)
        if budget is not None:
            mem.on_case = autosealed.budget_cut(rel, budget, ex.cancel)   # D-486 덧 12: the Run's own tokens × prices
        uid = ex.start(proj, rel, {"kind": evaluation.SUITE, "id": "sealed", "no_judge": True}, cases)
        ex.wait(uid)
        return mem.runs[uid]["cases"]
    app.state.shadow_exec = shadow_exec
    fake.context = lambda run, case: executor.fake_context(run, case) or shadow_exec.fake_context(run, case)   # EV-022 S-10: a Pack suite Run's context for the demo answers file (answer 019-04)
    fake.rule_for = lambda run: executor.fake_rule_pack(run) or shadow_exec.fake_rule_pack(run)   # EV-SUI-01 CHG-S05: the fake rule answers its own Pack's Runs only

    deleting = set()  # Project / release ids being deleted right now
    life = threading.Lock()  # a Run is either created before a delete marks its Project/release (and counted), or refused

    def start_checked(p, rel, run_input, cases):
        with life:
            if p["id"] in deleting or rel["id"] in deleting:
                raise HTTPException(status_code=409, detail="삭제 중인 Project·release는 실행할 수 없습니다")
            return executor.start(p, rel, run_input, cases)

    def key_stop(rel, mode):
        """D-492 덧 24 (packrt 196 (가)): one check for the three entrances — a real-mode start whose Pack declares a key name and whose key
        folder has no .env is refused with the path only (packs.missing_key_file; the file is never opened)."""
        miss = packs.missing_key_file(store, rel, mode)
        if miss:
            raise HTTPException(status_code=422, detail={"kind": "key-file", "message": packs.KEY_FILE_MISSING.format(path=miss)})

    def prepare_run(p, rid, run_in):
        """Release (re-read when it is a local path, so the code that actually runs is frozen, AC-09-C3) and the run input's cases."""
        if p["id"] in deleting or rid in deleting:
            raise HTTPException(status_code=409, detail="삭제 중인 Project·release는 실행할 수 없습니다")
        rel = store.release(rid)
        if not rel or rel["project_id"] != p["id"]:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        e = store.open_edit(p["id"])
        if e and e["release"] == rid:   # EV-022 manual p.9: an edit has no Run — confirm it, then run the confirmed version
            raise HTTPException(status_code=409, detail="편집 중인 버전은 실행하지 않습니다 — 확정한 버전에서 실행합니다")
        if rel["sourceKind"] == "agent_pack" and not Path(rel["pack"].get("copy", "")).is_dir():
            raise HTTPException(status_code=422, detail="Agent Pack 사본이 없습니다 — 다시 등록하세요")
        key_stop(rel, executor.settings.mode)   # D-492 덧 24: a real Run never starts without its Pack's key file (path only)
        if rel["sourceKind"] != "agent_pack" and rel["how"] == HOW_LABEL["local"]:
            try:
                cur = graphread.read_release(rel["path"], write_deny=[settings.data_dir])
            except graphread.ReadError as e:
                raise HTTPException(status_code=422, detail={"kind": e.kind, "message": f"실행하지 않았습니다 — {e}"})
            if cur["code_hash"] != rel["code_hash"]:
                rel = store.update_release_code(rel["id"], cur["code_hash"], cur["graph"])
        if run_in.kind == "data":
            if not p["data"]:
                raise HTTPException(status_code=422, detail="실행에 필요한 입력값이 없습니다")
            return rel, p["data"]["cases"], {"kind": "검증데이터", "id": "data", "name": p["data"]["name"]}
        if run_in.kind == "suite":   # EV-002 CHG-11: only for a Pack release of the same Pack id and entrypoint
            su = store.suite(run_in.id)
            if not su or su["project_id"] != p["id"]:
                raise HTTPException(status_code=422, detail="실행에 필요한 입력값이 없습니다")
            if not rel.get("pack") or rel["pack"]["id"] != su["pack_id"] or rel["pack"].get("entrypoint") != su["graph"]:
                raise HTTPException(status_code=422, detail="이 평가 suite는 같은 Pack(같은 entrypoint)의 release에서만 실행할 수 있습니다")
            review = {"case_policy": {str(i): suites.case_review(su, i) for i in range(len(su["cases"]))}}
            return rel, [{"input": c["input"], "id": c.get("id"), **({"then": c["then"]} if c.get("then") else {})} for c in su["cases"]], {"kind": "평가 suite", "id": su["id"], "name": su["name"],
                                                                      "suite_revision": su["suite_revision"], "dataset_hash": su["dataset_hash"], "review": review}
        s = store.input_set(run_in.id)
        if not s or s["project_id"] != p["id"]:
            raise HTTPException(status_code=422, detail="실행에 필요한 입력값이 없습니다")
        return rel, [{"input": x} for x in s["cases"]], {"kind": "입력값 세트", "id": s["id"], "name": s["name"]}

    @app.post("/api/projects/{pid}/runs", status_code=201)
    def start_run(pid: str, body: NewRun):
        p = need_project(pid)
        rel, cases, run_input = prepare_run(p, body.release, body.input)
        uid = start_checked(p, rel, run_input, cases)
        return run_view(store.run(uid))

    @app.post("/api/projects/{pid}/bundles", status_code=201)
    def start_bundle(pid: str, body: NewBundle):
        """Monitor comparison (P-11): one ordinary Run per chosen release on the same run input, started together (each on its
        own executor thread, the same sandbox, limit and key rules as any Run); the group is recorded for reopening."""
        p = need_project(pid)
        rids = list(dict.fromkeys(body.releases))
        if len(rids) < 2:
            raise HTTPException(status_code=422, detail="release를 2개 이상 고르세요")
        prepared = [prepare_run(p, rid, body.input) for rid in rids]   # nothing starts unless every release can run
        uids = [start_checked(p, rel, run_input, cases) for rel, cases, run_input in prepared]
        b = store.add_bundle(pid, prepared[0][2], uids)
        return {**b, "runs": [run_view(store.run(u)) for u in uids], "run_ids": uids}

    @app.post("/api/bundles/{bid}/cancel")
    def cancel_bundle(bid: str):
        b = store.bundle(bid)
        if not b:
            raise HTTPException(status_code=404, detail="묶음이 없습니다")
        for uid in b["runs"]:
            r = store.run(uid)
            if r and r["status"] == "실행 중":
                executor.cancel(uid)
        return {"ok": True}

    @app.get("/api/projects/{pid}/compare")
    def compare_view(pid: str, request: Request):
        """P-10 comparison table for one run input (?input=; default the latest Run's input); ?pick=<release>:<run> per row."""
        p = need_project(pid)
        base = ([{"id": "data", "kind": "검증데이터", "name": p["data"]["name"], "n": len(p["data"]["cases"])}] if p["data"] else []) + \
               [{"id": s["id"], "kind": "입력값 세트", "name": s["name"], "n": len(s["cases"])} for s in p["sets"]]
        # EV-012 CHG-71: same-content suites are one comparison input, listed once under the earliest registration
        groups = compare.suite_groups(p["suites"])
        seq = {s["id"]: i for i, s in enumerate(p["suites"], 1)}
        suite_in = [{"id": s["id"], "kind": "평가 suite", "name": s["name"], "n": len(s["cases"]), "seq": seq[s["id"]], "created_at": s["created_at"],
                     "group": [seq[x] for x in groups[s["id"]]]} for s in p["suites"]]
        inputs = base + [i for i in suite_in if groups[i["id"]][0] == i["id"]]
        rep = lambda i: groups[i][0] if i in groups else i
        q = request.query_params
        asked = rep(q.get("input")) if q.get("input") else None
        input_id = asked if asked in [i["id"] for i in inputs] else rep(compare.default_input(base + suite_in, p["runs"]))
        picks = dict(x.split(":", 1) for x in q.getlist("pick") if ":" in x)
        auto = auto_compare(pid, q["auto"]) if q.get("auto") else None
        if auto and auto.get("input"):   # EV-035 CHG-184: the comparison the wheel opened — its work suite and the two Runs it judged
            input_id = rep(auto["input"]) if auto["input"] in groups else auto["input"]
            picks = {**auto["picks"], **picks}
        rows, any_finished = compare.compare_rows(p["releases"], p["runs"], set(groups[input_id]) if input_id in groups else input_id, picks)
        kind = next((i["kind"] for i in inputs if i["id"] == input_id), None)
        suite = next((s for s in p["suites"] if s["id"] == input_id), None)
        sc = compare.scorecard_rows(rows, p["runs"], suite["cases"] if suite else ()) if kind == "평가 suite" else None   # EV-002 CHG-12
        views = version_views(p)   # EV-022 CHG-110: the 상태 column (manual p.24)
        for row in rows:
            row["state"] = (views.get(row["rel"]) or {}).get("badge")
        at = next((r for r in rows if r["anchor"]), None)   # EV-064 CHG-CF-1: the input's latest finished Run (the default pair's reference)
        out = {"inputs": inputs, "input": input_id, "rows": rows, "any_run": any_finished, "metrics": compare.METRICS, "scorecard": sc,
               "anchor": {"rel": at["rel"], "run": at["anchor_run"]} if at else None}
        if auto is not None:
            out["auto"] = auto
        return out

    def auto_compare(pid, name):
        """EV-035 CHG-184 (supplement A8 · 11/26): what P09 adds when the wheel opened it — per version the fix / keep sets, the flaky
        cases, the hidden cases (new version only, D-122), tokens · latency · LLM calls per case, the criteria line and the grading agent's
        recommendation (adopt / reject (자동), kept on the wheel's row — not a verdict). Read only."""
        w = next((x for x in (store.wheels(pid) if store.auto_ready else []) if f"A-{x['seq']}" == name), None)
        if not w:
            raise HTTPException(status_code=404, detail="자동 개선이 없습니다")
        recs = sorted([r for r in store.candidates(w["wheel_id"]) if r.get("recommendation")], key=lambda r: r["recommendation"].get("at") or 0)
        if not recs:
            return {"wheel": name, "waiting": True}
        r = recs[-1]
        rec, st = rec_shown(w["wheel_id"], r["recommendation"]), store.wheel_state(w["wheel_id"])   # D-659 덧
        b, n, hid = rec["columns"]["base"], rec["columns"]["new"], rec.get("hidden")
        ret = rec.get("retriever")   # EV-043 CHG-211: recall@10 in the fixed column, the confirm suite (test) in the hidden column — shown only
        new = store.release(rec["new"]["release"]) or {}
        base = store.release(rec["base"]["release"]) or {}
        cell = lambda c: {"tokens": c.get("tokens"), "latency": c.get("latency"), "calls": c.get("calls")}
        of = lambda c, a, t: f"{c[a]} / {c[t]}" if a in c and t in c else "—"   # D-551 덧 1 ③: an efficiency (or scoring-v1) record has no case counts
        return {"wheel": name, "kind": w.get("kind"), "input": (st.get("suites") or {}).get("work"),
                "picks": {k: v for k, v in ((base.get("id"), rec["base"]["run"]), (new.get("id"), rec["new"]["run"])) if k and v},
                "rows": {base.get("id"): {"fix": f"recall@10 {autocand.frac(b['retrieval']['recall'])}" + (f" · 예산 recall {autocand.frac(b['retrieval']['budget'])}" if (b['retrieval'] or {}).get("budget") else "")   # D-492 덧 16
                                          if ret else of(b, "fixed", "fix"), "keep": of(b, "kept", "keep"),
                                          "flaky": f"두 번 실행: {rec.get('flaky', 0)} case", "hidden": "—", **cell(b)},
                         new.get("id"): {"fix": f"recall@10 {autocand.frac(n['retrieval']['recall'])}" + (f" · 예산 recall {autocand.frac(n['retrieval']['budget'])}" if (n['retrieval'] or {}).get("budget") else "")
                                         if ret else of(n, "fixed", "fix"),
                                         "fixed_up": next((c["ok"] for c in rec["criteria"] if c["name"] in ("recall@10 ↑", autocand.BUDGET_UP)), False) is True if ret else ("fixed" in n and "fixed" in b and n["fixed"] > b["fixed"]), "keep": of(n, "kept", "keep") + (f" · 망가짐 {len(n['broken'])}" if "broken" in n else ""),
                                         "flaky": "—", "hidden": rec["confirm"]["text"] if ret else "없음(쌓이는 중)" if not hid else f"{hid['passed']} / {hid['n']} 통과" + (f" · 나빠짐 {len(hid['worse'])}" if hid["worse"] else ""),
                                         **cell(n)}},
                "base": {"id": base.get("id"), "name": base.get("name"), "label": rec["base"].get("label")},
                "new": {"id": new.get("id"), "name": new.get("name"), "label": rec["new"].get("label")},
                "criteria": rec["criteria"], "decision": rec["decision"], "failed": rec.get("failed") or [], "reasons": rec["reasons"],
                "hidden_none": not hid and not ret, "real": settings.mode == "real", "scoring": autocand.scoring_word(rec)}   # EV-089

    @app.get("/api/projects/{pid}/compare/details")
    def compare_details(pid: str, base: str, other: str):
        """'▸ 세부 사항' of the comparison page (manual p.24, CHG-110): case paths (parallel siblings in one order, 019d), final
        outputs that differ, the scorecard as named cells, the same-condition check and the lines that changed between the two
        Runs' Pack copies. Read only; nothing is stored."""
        p = need_project(pid)
        runs = []
        for uid in (base, other):
            r = store.run(uid)
            if not r or r["project_id"] != pid or r.get("candidate"):
                raise HTTPException(status_code=404, detail="이 Project의 Run이 아닙니다")
            if r["status"] == "실행 중":
                raise HTTPException(status_code=409, detail="실행이 끝난 Run끼리 비교합니다")
            runs.append(r)
        rb, ro = runs
        groups = compare.suite_groups(p["suites"])
        same_in = lambda i: set(groups.get(i, [i]))
        if ro["input"]["id"] not in same_in(rb["input"]["id"]):   # PRES-117: only Runs of the same run input are compared
            raise HTTPException(status_code=422, detail="같은 실행 입력으로 돈 Run끼리만 비교합니다")
        suite = next((s for s in p["suites"] if s["id"] == rb["input"]["id"]), None)
        rows, summary = compare.case_rows(rb, ro, suite["cases"] if suite else ())
        cb, co = compare.condition(rb), compare.condition(ro)
        differs = [k for k in cb if cb[k] != co.get(k)]
        rels = {r["id"]: r for r in p["releases"]}
        name = lambda r: (rels.get(r["rel"]) or {}).get("name") or r["rel_name"]
        lines = []
        ra, rc = rels.get(rb["rel"]), rels.get(ro["rel"])
        if ra and rc and ra.get("pack") and rc.get("pack") and rb.get("code_hash") != ro.get("code_hash"):
            lines = copy_hunks(run_copy(ra, rb), run_copy(rc, ro))
        sb, so = (rb.get("summary") or {}).get("scorecard"), (ro.get("summary") or {}).get("scorecard")
        return {"base": {"run": rb["uid"], "label": rb["label"], "release": name(rb)}, "other": {"run": ro["uid"], "label": ro["label"], "release": name(ro)},
                "cases": rows, "summary": {**summary, "final_differs": sum(r["final_differs"] for r in rows)},
                "line": compare.summary_line(name(ro), summary), "scorecard": compare.scorecard_diff(sb, so, models="모델 release" not in differs) if sb or so else {},   # EV-042 B-1
                "condition": {"same": not differs, "differs": differs, **({"differs_detail": compare.differs_detail(rb, ro, differs)} if differs else {}),
                              **({"notes": n} if not differs and (n := compare.condition_notes(rb, ro)) else {})},   # EV-050 CHG-W2-03
                "lines": lines}

    def copy_hunks(a, b):
        """Changed text lines between two Pack copies (file → hunks), for '바뀐 줄' (manual p.24)."""
        def files(root):
            root = Path(root)
            return {q.relative_to(root).as_posix(): q for q in sorted(root.rglob("*")) if q.is_file() and "__pycache__" not in q.parts} if root.is_dir() else {}
        fa, fb = files(a), files(b)
        out = []
        for path in sorted(set(fa) | set(fb)):
            xa, xb = fa.get(path), fb.get(path)
            ba, bb = xa.read_bytes() if xa else b"", xb.read_bytes() if xb else b""
            if ba == bb or len(ba) > 400_000 or len(bb) > 400_000 or b"\0" in ba + bb:
                continue
            hs = node_detail.hunks(ba.decode("utf-8", "replace"), bb.decode("utf-8", "replace"), context=3)
            out.append({"path": path, "lines": sum(max(len(h["old"]), len(h["new"])) for h in hs),
                        "hunks": [{**h, **{k: [guard.redact(x, keys.known_secrets()) for x in h.get(k) or []] for k in ("old", "new", "before", "after")}} for h in hs]})
        return out

    @app.get("/api/projects/{pid}/pack-diff")
    def pack_diff(pid: str, a: str, b: str):
        """EV-002 CHG-13: revision diff of two Agent Pack releases of this Project (read only)."""
        need_project(pid)
        ra, rb = store.release(a), store.release(b)
        if not ra or not rb or ra["project_id"] != pid or rb["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        try:
            return packdiff.revision_diff(ra, rb)
        except packdiff.DiffError as e:
            raise HTTPException(status_code=422, detail=guard.redact(str(e)))

    def need_run(uid):
        r = store.run(uid)
        if not r:
            raise HTTPException(status_code=404, detail="Run이 없습니다")
        return r

    @app.get("/api/runs/{uid}")
    def get_run(uid: str):
        return run_view(need_run(uid))

    # ---------------------------------------------------------------- EV-025 operational Runs (manual p.30, CHG-122 · 127), read only
    def ops_target(pid, rid):
        """An Agent Pack release of this Project and the deployment ids its current revision runs under: the tagged deployment
        rows of the same Pack and revision (an untagged dev deployment is not an operational one, S-16)."""
        need_project(pid)
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        pk = rel.get("pack")
        if not pk:
            raise HTTPException(status_code=422, detail="운영 Run은 Agent Pack 버전에만 있습니다")
        ids = sorted({d["deployment_id"] for d in store.deployments()
                      if d.get("pack_tag") and d["pack_id"] == pk.get("id") and d["pack_revision"] == pk.get("revision")})
        return rel, ids

    def ops_summary(rel):
        """EV-025 OPS-1b: which state fields the 결과 cell shows — the label file's 'result_summary' (read like the branch names)
        with the nodes of this version that write that state; None keeps the run_ended value."""
        raw = label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")).get("result_summary")
        return opsruns.summary_spec(raw, rel.get("graph"))

    def ops_input(rel):
        """EV-075 F-06: the label file's 'input_summary' (the alarm cell), read like result_summary; None keeps '—'."""
        return opsruns.input_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")).get("input_summary"))

    def ops_review(rel):
        """EV-077 (D-454): the label file's 'review_target' for this version's graph; None = every Run a target."""
        return opsruns.review_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")).get("review_target"), rel.get("graph"))

    def ops_kind(rel):
        """EV-077 (D-454 덧): the label file's 'run_kind' — the ratio compares the targets of one kind; None = the batch as one."""
        return opsruns.kind_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")).get("run_kind"))

    def ops_group(rel):
        """D-477 덧 4 · 5 (state-labels@1): the label file's optional 'group' — the 증거 세기 groups' input path; None = one group 전체."""
        return opsruns.group_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")).get("group"))

    def ops_minutes(rel):
        """D-478 #8 (state-labels@1): the label file's check_minutes · chosen_check · check_cause — all three or None."""
        return opsruns.minutes_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")))

    def ops_chosen(rel):
        """D-498 (state-labels@1): the label file's chosen_check alone — where a Run's chosen check is, for the check signals' truth."""
        return opsruns.chosen_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")))

    def ops_exception(rel):
        """D-546 덧 23 ② (state-labels@1): the label file's exception_check alone — where a start Run's exception check is."""
        return opsruns.exception_spec(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")))

    def ops_diagnose(rel):
        """D-549 덧 3 A (state-labels@1): the label file's 'diagnose' with chosen_check · check_cause — ④'s record-read rules, or None."""
        return autodeviate.spec_of(label_file((origin_release(rel).get("pack") or {}).get("work_folder"), rel["pack"].get("id")))

    def replay_inputs(pid, rid, limit):
        """D-477 (1): the version's operational Runs (its tagged deployments · revision), newest first, at most `limit` — each with its
        input value (None: no raw value · expired) and its 증거 세기 group. Read only (no promotion row)."""
        rel, ids = ops_target(pid, rid)
        if not ids:
            return []
        r = opsruns.reader(settings)
        gspec, out, cur = ops_group(rel), [], None
        while len(out) < limit:
            runs, nxt = r.list(ids, rel["pack"]["revision"], cur, rspec=ops_review(rel))   # D-476 덧 7: each Run's review target
            out += runs
            if not nxt:
                break
            at, _, rr = nxt.partition("|")
            cur = (at, rr)
        rows = []
        for x in out[:limit]:
            value = None if x.get("input_missing") else r.run_input(x["run_id"])
            rows.append({"run_id": x["run_id"], "input": value, "group": opsruns.group_of(value, gspec) if value is not None else None,
                         "target": x.get("target", True)})
        return rows

    def ops_outputs(pid, rid, run_id):
        """D-477 (1): one operational Run's output of that time — {"final": its ended output, "nodes": {node: its last output}} read
        from the payload folder; None when the Run is not this version's or its final output cannot be read."""
        rel, ids = ops_target(pid, rid)
        r = opsruns.reader(settings)
        d = r.detail(run_id, rel.get("graph"))
        if not d or d["deployment_id"] not in ids or d["pack_revision"] != rel["pack"]["revision"]:
            return None
        read = lambda ref: opsruns.read_payload(r.root, ref, r.trace.expired([ref.split(":", 1)[1]] if ref and ":" in ref else []))[0] if ref else None
        final = read(d.get("output_ref"))
        if final is None:
            return None
        nodes = {}
        for v in d["visits"]:
            if v.get("status") == "done" and v.get("output_ref"):
                nodes[v["node"]] = read(v["output_ref"])
        return {"final": final, "nodes": nodes}

    def session_start(pid, rid, run_id):
        """D-525: the start Run of the session a judge Run belongs to — the person's check record that names run_id among its judge
        Runs, its start Run's input and ended output (this version's, from the payload folder) as {"run_id", "input", "output"};
        {"why"} when there is no start Run to use; None when no record names run_id a judge Run."""
        try:
            r = opsruns.reader(settings)
            rec = next((x for x in r.check_records() if run_id in (x.get("judge") or [])), None)
            if rec is None:
                return None
            start = rec.get("start")
            if not start or start == run_id:
                return {"why": "기록에 시작 Run 없음"}
            out = ops_outputs(pid, rid, start)
            value = r.run_input(start) if out else None
        except opsruns.OpsUnavailable as e:
            return {"why": f"운영 기록을 읽을 수 없음({opsruns.REASON.get(e.kind, e.kind)})"}
        if out is None:
            return {"why": f"시작 Run {start}의 출력을 읽을 수 없음(이 버전의 운영 Run이 아니거나 원문 없음)"}
        if value is None:
            return {"why": f"시작 Run {start}의 입력 원문 없음"}
        return {"run_id": start, "input": value, "output": out["final"]}

    def approved_projects(rel, memo=None):
        """D-168 (EV-034): the Projects whose deploy approval runs this release's revision — the decision evidence release of every
        tagged deployment row of the same Pack id and revision that came from a deploy_approved decision (a deleted release is left
        out, as deploy_stage leaves its row out), in row order. None when there is none (bootstrap rows only): no scope."""
        pk = (rel or {}).get("pack") or {}
        out = []
        memo = memo if memo is not None else {}   # state-n1: /api/state's memo keeps the deployments · decisions · releases it read
        decs, by_id = memo.setdefault("decision", {}), memo.get("rel_by_id") or {}
        for row in memo["deployments"] if "deployments" in memo else store.deployments():
            if not row.get("pack_tag") or row["pack_id"] != pk.get("id") or row["pack_revision"] != pk.get("revision"):
                continue
            if row["decision_id"] not in decs:
                decs[row["decision_id"]] = store.decision(row["decision_id"])
            d = decs[row["decision_id"]]
            if not d or d["decision"] != "deploy_approved":
                continue
            rid_ = (d.get("evidence") or {}).get("release")
            r = by_id[rid_] if rid_ in by_id else store.release(rid_)
            if r and r["project_id"] not in out:
                out.append(r["project_id"])
        return out or None

    def elsewhere(pid, rel, memo=None):
        """D-168: the approved Projects' names (' · ') when this Project is not one of them — rollback and the auto cycle belong
        there; None otherwise (this Project is one of them, or no deploy approval decides it). memo["names"]: /api/state's names."""
        ps = approved_projects(rel, memo)
        if not ps or pid in ps:
            return None
        names = (memo or {}).get("names") or {}
        return " · ".join(names[x] if x in names else (store.project(x) or {}).get("name", x) for x in ps)

    def project_elsewhere(pid, memo=None):
        """D-168: elsewhere() for the Project's 운영 version — the screen's (EV-052 CHG-227: operational_of, as auto_targets picks it)."""
        m = memo or {}   # state-n1: the answer's releases · deployments, read once for every Project
        rels = m["by_pid"][pid] if pid in m.get("by_pid", {}) else store.releases(pid)
        op = operational_of(pid, rels, m["deployments"] if "deployments" in m else store.deployments(), memo)[0]
        return elsewhere(pid, m["rel_by_id"][op] if op in m.get("rel_by_id", {}) else store.release(op), memo) if op else None

    AUTO_ELSEWHERE = "자동 진단은 배포 승인을 받은 Project {}에서 합니다"
    CHECK_LINE = re.compile(r"사람 (체크|✗ 라벨) \d+건")   # EV-065 CHG-263a: a wheel's ① or join line of a person's check
    CHECK_COUNT = re.compile(r"사람 (?:체크|✗ 라벨) (\d+)건")

    @app.get("/api/projects/{pid}/releases/{rid}/ops-runs")
    def ops_runs(pid: str, rid: str, before: str = ""):
        rel, ids = ops_target(pid, rid)
        base = {"deployment_ids": ids, "revision": rel["pack"]["revision"]}
        if not ids:
            return {**base, "connected": True, "runs": [], "next": None}
        cur = None
        if before:   # the cursor the previous page gave: '<started_at ISO>|<run_id>'
            at, _, rid_ = before.partition("|")
            from datetime import datetime
            try:
                datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=422, detail="이어 읽기 위치가 맞지 않습니다")
            if not re.fullmatch(r"run_[0-9A-HJKMNP-TV-Z]{26}", rid_):
                raise HTTPException(status_code=422, detail="이어 읽기 위치가 맞지 않습니다")
            cur = (at, rid_)
        try:
            runs, nxt = opsruns.reader(settings).list(ids, rel["pack"]["revision"], cur, spec=ops_summary(rel), ispec=ops_input(rel), rspec=ops_review(rel))
        except opsruns.OpsUnavailable as e:   # the evaluation Runs stay as they are; the reason kind only, never the DSN (PRES-129)
            return {**base, "connected": False, "reason": e.kind, "reason_text": opsruns.REASON.get(e.kind, e.kind), "runs": [], "next": None,
                    "auto": safe_band(pid, rid)}
        up = {x["source_run_id"] for x in store.promotions(pid)}   # OPS-3: '올림' on a row already put up
        for x in runs:
            x["promoted"] = x["run_id"] in up
        auto = safe_band(pid, rid)
        try:   # EV-088: this version's Runs among same-revision versions (trace drs) — the rows · labels where they were made
            vrows, vlabels = autodiag.version_rows(store, pid, rel, {x["run_id"]: x.get("data_release_id") for x in runs})
        except Exception:   # no diagnosis tables (the auto cycle never set up)
            vrows, vlabels = {}, []
        try:   # packrt 138 ② (D-412): a Run with a person's label or check never folds — whether the diagnosis is on, off or elsewhere
            kept = {lb["run_id"] for lb in vlabels} | set(diag.check_kinds(pid, rel))
        except Exception:   # no label tables (the auto cycle never set up): nothing kept by a person
            kept = set()
        for x in runs:
            x["kept"] = x["run_id"] in kept
        if auto["state"] not in ("off", "elsewhere"):   # EV-030 CHG-152: the 진단(자동) cell (none while off — or elsewhere, D-168)
            diags = dict(vrows)   # EV-065 CHG-263a: general rows only; EV-088: a row made under a same-revision version is read as it is
            view = by_new_rule(pid, rel, list(diags.values()))   # EV-077 (가)
            diags = {r: view.get(r, d) for r, d in diags.items()}
            labels = {lb["run_id"]: lb for lb in vlabels}   # the last label of each Run counts (D-92)
            names = {r["id"]: r["name"] for r in store.releases(pid)}
            checks = diag.check_kinds(pid, rel)   # EV-065 CHG-263a: '사람 체크 · <종류>'
            for x in runs:   # EV-077: a procedure record shows no diagnosis — a person's check or label on it still does (D-412)
                lb, ck = labels.get(x["run_id"]), checks.get(x["run_id"])
                x["diagnosis"] = autodiag.cell(diags.get(x["run_id"]) if x["target"] else None, lb, ck) if x["target"] or lb or ck else {"text": None, "signal": None}
                src = (diags.get(x["run_id"]) or {}).get("release_id")
                if x["diagnosis"].get("text") and src and src != rid:   # EV-088: diagnosed under the version operational then
                    x["diagnosis"]["from"] = autodiag.FROM_OTHER.format(name=names.get(src, src))
        return {**base, "connected": True, "runs": runs, "next": nxt, "auto": auto}

    @app.get("/api/projects/{pid}/releases/{rid}/ops-runs/{run_id}")
    def ops_run(pid: str, rid: str, run_id: str):
        rel, ids = ops_target(pid, rid)
        try:
            d = opsruns.reader(settings).detail(run_id, rel.get("graph"), ops_summary(rel), ops_input(rel))
        except opsruns.OpsUnavailable as e:
            raise HTTPException(status_code=503, detail=f"운영 기록을 읽을 수 없습니다 — {opsruns.REASON.get(e.kind, e.kind)}")
        if not d or d["deployment_id"] not in ids or d["pack_revision"] != rel["pack"]["revision"]:
            raise HTTPException(status_code=404, detail="이 버전의 운영 Run이 아닙니다")
        return d

    # ---------------------------------------------------------------- EV-030 automatic diagnosis (CHG-151 · 152, supplement A1 · A2)
    def last_suite_run(pid, rid):
        """The release's last finished evaluation-suite Run (the ratio signal's comparison, D-90)."""
        return store.last_suite_run(rid)   # the stored Run (label · cases with their route visits) — all the ratio reads

    skipped = {}   # EV-052 CHG-228: {project id: the note last logged} — a note logs once until it clears

    def auto_targets():
        """Every Project's 운영 Agent Pack version and its tagged deployment ids (the 운영 Run list's condition, KD-3)."""
        out, rows = [], store.deployments()
        if not any(d.get("pack_tag") for d in rows):   # no tagged deployment, no 운영 version: nothing to read (cheap pass)
            return out
        tagged = {(d["pack_id"], d["pack_revision"]) for d in rows if d.get("pack_tag")}
        memo = {}   # EV-052 PRES-246: one approval_progress per decision and one store.rollbacks() for the whole pass
        for pid in store.pack_projects(tagged):       # only the Projects holding a tagged Pack revision (AC-1 review-3: a pass that read
                                                      # every Project's releases on the one PostgreSQL connection slowed the screens)
            rels = store.releases(pid)
            op, _, info, _ = operational_of(pid, rels, rows, memo)   # EV-052 CHG-227: the screen's 운영 (switch times, rollbacks)
            if info.get("note"):   # CHG-228: undecided — skip the Project this pass (before the D-168 check), one log line per note
                if skipped.get(pid) != info["note"]:
                    skipped[pid] = info["note"]
                    print(f"llmops 자동 진단 건너뜀 — Project {(store.project(pid) or {}).get('name', pid)}: {info['note']}", file=sys.stderr, flush=True)
                continue
            skipped.pop(pid, None)
            rel = store.release(op) if op else None
            if rel and elsewhere(pid, rel):              # D-168: the auto cycle belongs to the deploy-approved Project
                continue
            pk = (rel or {}).get("pack") or {}
            ids = sorted({d["deployment_id"] for d in rows if d.get("pack_tag") and d["pack_id"] == pk.get("id") and d["pack_revision"] == pk.get("revision")})
            if rel and ids:   # the evaluation Run (the ratio's comparison) is read only when there are Runs to diagnose
                out.append({"project_id": pid, "release": rel, "deployment_ids": ids, "spec": ops_summary(rel),
                            "eval_run": (lambda pid=pid, rid=rel["id"]: last_suite_run(pid, rid)),
                            "review": ops_review(rel), "run_kind": ops_kind(rel),   # EV-077: targets only · the ratio by kind
                            "eval_runs": (lambda rid=rel["id"]: store.suite_runs(rid)),
                            "since": info.get("switch_at"), "releases": rels,   # EV-088: when this version became 운영 (a no-drs label's rule) · the Project's releases read once
                            "line": kglink.line_of(settings.data_dir, rels, rel) if settings.kg_link else [],   # D-597: the versions before it that differ only in the graph
                            "prev_op": versions.previous_operational(info, op)})   # D-675: the version 운영 just before (read when on that line)
        return out

    def register_subset(pid, base_id, keep, rows):
        """EV-035 CHG-180: the wheel's work · hidden suite by the usual registration (same content → that suite, unmarked)."""
        base = store.suite(base_id)
        src = suites.copy_dir(settings.data_dir, base["id"])
        rel = next(r for r in store.releases(pid) if (r.get("pack") or {}).get("id") == base["pack_id"])
        stage = settings.data_dir / "staging" / f"auto-{uuid.uuid4().hex[:8]}"
        try:
            suites.build_subset(src, stage, base["folder"], keep, rows)
            review_nodes = [n["name"] for n in (rel.get("graph") or {}).get("nodes", []) if str(n.get("kind", "")).startswith("human_review")]
            su = suites.read_suite(stage, base["folder"], rel["pack"], review_nodes)
            su.pop("source")
            su["derived_from"] = {"suite": base["id"], "wheel": True, "kept": len(keep), "added": len(rows)}   # the tag reads S-k's kept cases + the wheel's cases
            try:
                return {"id": register_suite(pid, su, stage / "evals" / base["folder"])["id"], "new": True}
            except HTTPException as e:
                if e.status_code != 409:
                    raise
                return {"id": next(s for s in store.suites(pid) if suite_key(s) == suite_key(su))["id"], "new": False}
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def start_suite_run(pid, rid, sid, tag, judge=True):
        """EV-035: an ordinary evaluation Run started by the wheel (the same checks as 실행 ▶), marked with what it is for.
        EV-069: judge=False scores it without the judge (the efficiency wheel — A3-B2)."""
        p = need_project(pid)
        rel, cases, run_input = prepare_run(p, rid, types.SimpleNamespace(kind="suite", id=sid))
        if not judge:
            run_input = {**run_input, "no_judge": True}
        uid = start_checked(p, rel, run_input, cases)
        store.set_run_meta(uid, "auto", tag)
        return uid

    def shadow_alive(uid):
        """A row run still going: its thread is alive and it is within every case's time limit plus a minute (else it is cancelled and
        the wheel runs it again — a run thread that died or hung never holds the wheel)."""
        t, r = shadow_exec.threads.get(uid), shadow_store.run(uid)
        if not t or not t.is_alive() or not r:
            return False
        if time.time() - r["created_at"] > len(r.get("cases") or []) * shadow_exec.case_limit_s + 60:
            shadow_exec.cancel(uid)
            return False
        return True

    def candidate_lines(rel, ops):
        """EV-035 CHG-182: a row's change lines in the person's edit shape (source auto) and the same structure check (editing.evaluate)."""
        base = rel["pack"]["copy"]
        got = editing.edited_doc(base, [])
        doc = got[0] if got else None
        before = (lambda node, param: editing.base_value(doc, rel["pack"].get("entrypoint"), node, param)) if doc else None
        lines = editing.add([], ops, source="auto", before=before)
        view, revision, _, _, savable = editing.evaluate(base, lines, rel["pack"].get("entrypoint"))
        if not savable:
            view = {**view, "ok": False}
        return lines, revision, view

    def row_folder(row):
        return settings.data_dir / "auto" / str(row["wheel_id"]) / str(row["candidate_id"]) / "pack"

    def index_nodes(rel, row):
        """EV-051: the retrieve@2 nodes whose index options the row's set ops change, {node: {chunk_size, overlap}} (what was set)."""
        kinds = {n["name"]: str(n.get("kind", "")) for n in (rel.get("graph") or {}).get("nodes", [])}
        out = {}
        for ln in row.get("lines") or []:
            for o in ln.get("ops") or []:
                param = str(o.get("param", ""))
                if o.get("op") == "set" and param.split(".")[0].split("[")[0] == "index" and kinds.get(o.get("node"), "").startswith("retrieve@2"):
                    got = out.setdefault(o["node"], {})
                    got.update(o["value"] if param == "index" and isinstance(o.get("value"), dict) else {param.split(".", 1)[-1]: o.get("value")})
                elif o.get("op") == "set_option" and o.get("option") == "split" and kinds.get(o.get("node"), "").startswith("retrieve@2"):
                    out.setdefault(o["node"], {})["split"] = o.get("value")   # D-492 덧 15 · 28: the manual DB's cut makes it again too
        return {n: {**{k: v.get(k) for k in ("chunk_size", "overlap")}, **({"split": v["split"]} if "split" in v else {})} for n, v in out.items()}

    def apply_index_builds(folder, builds):
        for b in builds:
            packs.add_index_release(folder, b["store"], b["release_id"], settings.data_dir / packs.INDEX_BUILDS if b.get("in_builds") else None)

    def index_into(rel, lines, folder, before_build, after_build=None):
        """EV-051 CHG-W2-05 ~ 08 (D-292 · D-293) · D-492 덧 28: the vector DB versions a Pack folder made from `rel` with `lines` needs —
        for the retrieve@2 nodes whose index options (or declared split) the lines change: looked for under the folder's release_roots
        and the index-builds, built only when none has the build hash (before_build(plan) says why not, or None) — written into the
        folder's config. [] for lines that change none. The wheel's rows and a person's confirmed edit both come this way. Raises
        packs.IndexStepError."""
        nodes = index_nodes(rel, {"lines": lines})
        if not nodes:
            return []
        pack, builds_root = rel["pack"], settings.data_dir / packs.INDEX_BUILDS
        packs.clear_staging(settings.data_dir)
        answer = packs.probe_answer(settings.data_dir, pack, folder, keys.known_secrets())
        # the stores of the nodes the row changed (the base graph's params.store) — or the node itself, as the probe names it
        stores = {(n.get("params") or {}).get("store") for n in (rel.get("graph") or {}).get("nodes", []) if n["name"] in nodes}
        pg = next((u["store"] for u in packs.index_unchecked_of(answer) if u["store"] in stores or u["node"] in nodes), None)
        if pg:   # EV-051 CHG-W2-16 (D-336): wheel ② evaluates on flat-local vector DBs only — a pg one is never read or written here
            raise packs.IndexStepError(f"pg 벡터 DB {pg} — 바퀴 ②는 flat-local 벡터 DB만")
        missing = packs.index_missing_of(answer)
        roots = packs.config_of(folder).get("release_roots") or []
        made = []
        for m in missing:
            if not m.get("build_sha256"):
                raise packs.IndexStepError("기준 version에 corpus 없음")
            hit = packs.find_index_version([*roots, builds_root], m["build_sha256"])
            if hit:
                rid, where, status = hit[0], hit[1], "reused"
            else:
                corpus = packs.base_corpus(folder, m["store"])
                corpus_folder = packs.find_corpus(roots, corpus)
                # EV-051 CHG-W2-17 ~ 20 (D-346): the build's mode, model, size and key name — then the budget, then the spend
                plan = packs.build_plan(folder, m["store"], m["node"], corpus_folder, settings.mode == "real", packs.key_folder(store, rel))   # EV-061: the origin's key file (read only for a real plan)
                why = before_build(plan)   # counted only for a build that will start (slice critic N-4)
                if why:
                    raise packs.IndexStepError(why)
                report = {}
                try:
                    rid, status = packs.index_build(settings.data_dir, pack, folder, pack.get("entrypoint") or pack["entrypoints"][0], m["node"],
                                                    corpus_folder, m["build_sha256"], corpus, keys.known_secrets(), plan=plan, report=report)
                except Exception:   # any failure after the build may have spent money (slice review B-W23-1)
                    if after_build:
                        after_build(plan, {**report, "failed": True})   # a failed real build costs at least its reservation (B-F1-3)
                    raise
                if after_build:
                    after_build(plan, report)
                where = builds_root / rid
            b = {"store": m["store"], "node": m["node"], "release_id": rid, **nodes.get(m["node"], {}), "status": status,
                 "in_builds": Path(where).resolve().parent == builds_root.resolve()}
            apply_index_builds(folder, [b])
            made.append(b)
        if packs.index_missing_of(packs.probe_answer(settings.data_dir, pack, folder, keys.known_secrets())):
            raise packs.IndexStepError("벡터 DB version을 다시 확인하지 못함(번호 겹침일 수 있음)")
        return made

    def index_step(rel, row, before_build, after_build=None):
        """EV-051 CHG-W2-05 ~ 08 (D-292 · D-293): for a row that changes a retrieve@2 node's index, the candidate copy gets the vector
        DB version of the new recipe (index_into) and the Pack values the run uses are read again from the changed config, as
        registration reads them. None for any other row. Raises packs.IndexStepError (the row's discard reason)."""
        if not index_nodes(rel, row):
            return None
        folder = row_folder(row)
        if not folder.is_dir():
            packs.clear_staging(settings.data_dir)
            editing.materialize(rel["pack"]["copy"], row["lines"], folder)
        made = index_into(rel, row["lines"], folder, before_build, after_build)
        got = packs.read_pack(folder, str(folder / packs.CONFIG_COPY), need_config=True, dsn=packs.runtime_dsn(settings.db_env_file))["pack"]
        if got["revision"] != row["revision"]:
            raise packs.IndexStepError("후보 사본 revision이 다름")
        return {"index_builds": made, "index_pack": {k: got[k] for k in ("config_sha256", "config_text", "bindings", "config_paths", "uses_postgres")}}

    def base_index_missing(rel):
        """EV-051 CHG-W2-10: the base version's missing vector DB versions (probe index_missing) — [] when its graph has no retrieve@2."""
        if not any(str(n.get("kind", "")).startswith("retrieve@2") for n in (rel.get("graph") or {}).get("nodes", [])):
            return []
        return packs.index_missing_of(packs.probe_answer(settings.data_dir, rel["pack"], rel["pack"]["copy"], keys.known_secrets()))

    def run_row(pid, rel, row, work_sid, case_ids, judge=True):
        """One run of a row's change over some cases of the work suite (judge off for the cheap test) — kept in memory only."""
        folder = row_folder(row)
        idx = (row.get("check_result") or {}).get("index_builds") or []
        if not folder.is_dir():
            editing.materialize(rel["pack"]["copy"], row["lines"], folder)
            apply_index_builds(folder, idx)   # EV-051: the row's versions again, never a copy without them
        su = store.suite(work_sid)
        sub = {**su, "cases": [c for c in su["cases"] if c["id"] in set(case_ids)], **({} if judge else {"judge": False})}
        sid = shadow_store.put_suite(sub)
        run_input = {"kind": "평가 suite", "id": sid, "wheel_suite": work_sid, "name": su["name"], "suite_revision": su["suite_revision"], "dataset_hash": su["dataset_hash"],
                     "review": {"case_policy": {str(i): suites.case_review(sub, i) for i in range(len(sub["cases"]))}}}
        r = {**rel, "pack": {**rel["pack"], **((row.get("check_result") or {}).get("index_pack") or {}), "copy": str(folder), "revision": row["revision"]}}
        return shadow_exec.start(need_project(pid), r, run_input, [{"input": c["input"], "id": c.get("id"), **({"then": c["then"]} if c.get("then") else {})} for c in sub["cases"]])

    def make_version(pid, rel, row, auto, memo):
        """EV-035 CHG-183: the winner becomes V<n+1> by the registration a person's '새 버전 생성' uses, with its change lines as an edit
        that is already confirmed (the deploy approval reads the chain from it). A failed registration keeps nothing."""
        p = store.project(pid)
        eid = store.add_confirmed_edit(pid, rel["id"], rel["pack"]["copy"], rel["pack"]["revision"], row["lines"], row["revision"], row["check_result"], None, memo)
        dest = settings.data_dir / "edits" / str(eid) / "pack"
        origin = {"release": rel["name"], "rid": rel["id"], "revision": rel["pack"]["revision"], "edit_id": eid, "memo": memo, "at": time.strftime("%m-%d %H:%M"),
                  "changes": [f"{l.get('node') + ' ' if l.get('node') else ''}{l.get('summary', '')}".strip() for l in row["lines"]],
                  "nodes": sorted({l["node"] for l in row["lines"] if l.get("node")} | {row["node"]}), "auto": auto}
        try:
            editing.materialize(rel["pack"]["copy"], row["lines"], dest)
            apply_index_builds(dest, (row.get("check_result") or {}).get("index_builds") or [])   # EV-051 CHG-W2-08
            out = register_folder(pid, versions.next_name(p["releases"]), dest, CONFIRM_LABEL, {"from": origin})
        except Exception:
            shutil.rmtree(dest.parent, ignore_errors=True)
            store.drop_unregistered_edit(eid)
            raise
        store.set_edit_result(eid, out["id"])
        return store.release(out["id"])

    def fixed_flow_check(rel, lines, unconfirmed=None):
        """EV-069 CHG-EF-4: the row's Pack (the change lines on a temporary copy of the base) against the base's confirmed fixed flow.
        unconfirmed (EV-079, D-463 덧 9): the answer when the base has none confirmed — the accuracy wheel passes [] (nothing to check)."""
        row = store.fixed_flow(rel["id"])
        if not row or not row.get("confirmed_by"):
            return [{"message": "기준 버전의 고정 흐름이 확인되지 않음"}] if unconfirmed is None else unconfirmed
        tmp = Path(tempfile.mkdtemp(prefix="llmops-ff-"))
        try:
            editing.materialize(rel["pack"]["copy"], lines, tmp / "pack")
            return fixedflow.check(row["items"]["items"], tmp / "pack")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def ff_row(rel):
        """D-613: the version's fixed flow row — with the graph link on, the versions before it that differ only in the graph count."""
        return fixedflow.ensure(store, rel, kglink.line_of(settings.data_dir, store.releases(rel["project_id"]), rel) if settings.kg_link else ())

    def fixed_flow_state(rel):
        """EV-079 (D-463 덧 15): the accuracy wheel's view of the version's fixed flow — automain.FF_NONE when the Pack lists none,
        else 확인함 · 확인 필요 · 읽지 못함 (read now for its revision, as the efficiency start does)."""
        row = ff_row(rel)
        if not row["items"].get("errors") and not row["items"].get("items"):
            return automain.FF_NONE
        return fixedflow.state(row)

    def write_run(pid, rel, run, tag):
        """EV-035 CHG-183 (D-121): a row run written once as the new version's evaluation Run — the same cases and summary, no new
        execution; the revision is the same (the row's change lines are what the version was registered from)."""
        meta = {k: v for k, v in run.items() if k not in ("uid", "project_id", "release_id", "status", "created_at", "finished_at", "cases", "summary", "label", "notice")}
        meta.update(rel_name=rel["name"], code=rel["code"], code_hash=rel["code_hash"], graph=rel["graph"], graphId=rel["graph"]["id"], auto=tag,
                    path=rel.get("path"), how=rel.get("how"))   # the new version's, not the starting version's
        inp = dict(meta.get("input") or {})
        inp["id"] = inp.pop("wheel_suite")   # the registered work suite, not the in-memory copy the row ran on
        meta["input"] = inp
        uid = store.create_run(pid, rel["id"], meta, run["cases"])
        store.finish_run(uid, run["status"], run.get("notice"), summary=run.get("summary"), cases=run["cases"])
        return store.run(uid)["label"]

    main = automain.Main(store, settings, types.SimpleNamespace(promote=lambda *a: promote_run(*a), register_subset=register_subset,   # promote_run is defined below
                                                                  start_suite_run=start_suite_run, fixed_flow_check=fixed_flow_check, fixed_flow_state=fixed_flow_state, run=store.run, shadow_run=shadow_store.run, shadow_drop=shadow_store.drop,
                                                                  version_view=lambda pid, rid: version_views(store.project(pid)).get(rid),
                                                                  shadow_alive=shadow_alive,
                                                                  auto_apply=lambda pid, rid, body: deploy_approval(pid, rid, body),   # D-565 (dev-2): defined below
                                                                  cancel_run=lambda uid: (shadow_exec if str(uid).startswith("cand-") else executor).cancel(uid),   # D-476 덧 20
                                                                  replay_inputs=replay_inputs, ops_outputs=ops_outputs,   # D-477 (1)
                                                                  session_start=session_start,   # D-525
                                                                  sealed_run=sealed_run,   # D-477 (2)
                                                                  group_spec=lambda rid: ops_group(store.release(rid)) if (store.release(rid) or {}).get("pack") else None,
                                                                  minutes_spec=lambda rid: ops_minutes(store.release(rid)) if (store.release(rid) or {}).get("pack") else None,   # D-478 #8
                                                                  chosen_spec=lambda rid: ops_chosen(store.release(rid)) if (store.release(rid) or {}).get("pack") else None,   # D-498
                                                                  exception_spec=lambda rid: ops_exception(store.release(rid)) if (store.release(rid) or {}).get("pack") else None,   # D-546 덧 23 ②
                                                                  diagnose_spec=lambda rid: ops_diagnose(store.release(rid)) if (store.release(rid) or {}).get("pack") else None,   # D-549 덧 3 A
                                                                  candidate_lines=candidate_lines, run_row=run_row, make_version=make_version, write_run=write_run,
                                                                  screen=lambda pid, rel, cases, combos, timeout: shadow_exec.screen(rel, cases, combos, timeout),   # D-549 덧 13
                                                                  index_step=index_step, index_into=index_into, index_nodes=index_nodes, base_index_missing=base_index_missing,   # EV-051
                                                                  decide=app.state.gateway.decide,   # D-462 B: the quality review's Jev call
                                                                  judge_node=lambda pid, text, i, o, node=None: gatewaymod.judge_node(app.state.gateway, text, i, o, *project_llm((store.project(pid) or {}).get("scoring")), node=node),
                                                                  ask_agent=lambda pid, node, prompt, cases, others=(), history=(): autohyp.ask_agent(   # EV-079: the agent model · nodes before
                                                                      app.state.gateway, *agent_llm(getattr(settings, "auto_agent_model", None), (store.project(pid) or {}).get("scoring")),
                                                                      node, prompt, cases, show_index=settings.auto_index_real, others=others, history=history),
                                                                  ask_structure_agent=lambda pid, payload: autohyp.ask_structure_agent(app.state.gateway, settings.auto_agent_model, payload)),   # D-462 A
                         judge_model=lambda pid: project_llm((store.project(pid) or {}).get("scoring"))[1])
    app.state.automain = main
    def kg_register(tag, folder):
        """D-588: the Pack at a graph tag as the next version of the linked Project."""
        pid = settings.kg_link[0]
        return register_folder(pid, versions.next_name(store.project(pid)["releases"]), str(folder), HOW_LABEL["local"], {"graph_tag": tag})

    def kg_config():
        """The config of the version the deployment's last ledger row runs — never the Project's first version, which may be another store."""
        pid, dep = settings.kg_link[:2]
        rev = [d for d in store.deployments() if d["deployment_id"] == dep][-1]["pack_revision"]
        return next(r for r in store.releases(pid) if (r.get("pack") or {}).get("revision") == rev)["pack"]["config_path"]

    def kg_running_drs():
        """The data release the deployment's server runs under (its /info), None when it cannot be asked."""
        try:
            with urllib.request.urlopen(settings.kg_link[2].rstrip("/") + "/info", timeout=5) as r:
                return json.loads(r.read()).get("data_release_id")
        except (OSError, ValueError):
            return None

    app.state.kg_running_drs = kg_running_drs

    def kg_link_pass():
        from . import admin
        kglink.run(None, store, kg_register, admin.registration, config_of=kg_config, running_drs=kg_running_drs, signal_dir=settings.kg_signal_dir, repo=settings.kg_pack_repo,
                   data_dir=settings.data_dir, deployment_id=settings.kg_link[1], runtime_version=admin.runtime_version, key_file=settings.openai_env_file)

    diag = autodiag.Diagnoser(store, settings, app.state.gateway, auto_targets, wheels=autocycle.Wheels(store, autodiag.cell, main),
                              kg_link=kg_link_pass if settings.kg_link else None)
    app.state.autodiag = diag

    recounted = {}   # EV-077 (가): (release, revision, batch) → {run_id: the row as the new rule shows it, or None for a procedure Run}

    def by_new_rule(pid, rel, rows):
        """packrt 138 ① (가): the general rows diagnosed before EV-077 (no kind in their ratio — the batch's ratio over every Run) as the
        label file's review_target · run_kind show them now: a procedure Run's row → None, a target's ratio counted again for the targets
        of its batch only (autodiag.ratio_by_kind with this release's suite Runs). Read only, once per batch for this server."""
        review, kspec = ops_review(rel), ops_kind(rel)
        if not review and not kspec:
            return {}
        out = {}
        for batch in {d["batch"] for d in rows if autodiag.before_targets(d)}:
            key = (rel["id"], rel["pack"]["revision"], batch, json.dumps(review, default=sorted, sort_keys=True), kspec)   # 139 ③: a new label file counts again
            got = recounted.get(key)
            if got is None:
                mates = [d for d in rows if d["batch"] == batch and autodiag.before_targets(d)]
                try:
                    ops = opsruns.reader(settings)
                    got = autodiag.recount(mates, ops, rel.get("graph") or {}, review, kspec, store.suite_runs(rel["id"]))
                except Exception:   # packrt 139 ①: not now (logDB down or anything else) — the stored rows show as they are, the next read tries again
                    continue
                if all(d["run_id"] in got for d in mates):   # D-480 덧 9: a batch with a skipped Run is not kept — the next read counts again
                    recounted[key] = got
            out.update(got)
        return out

    def safe_band(pid, rid):
        """D-468: the band never blocks the 운영 Run list — a band that fails is no band (the error kind to the server log only)."""
        try:
            return auto_band(pid, rid)
        except Exception as e:   # noqa: BLE001 - the list answers without its band
            print(f"llmops 운영 띠 계산 건너뜀 — {type(e).__name__}", file=sys.stderr, flush=True)
            return {"state": "none"}

    def auto_band(pid, rid):
        """The band above the 운영 Run table (CHG-152): off · stopped · running k / n · done summary of the last batch · none."""
        if not store.auto_ready:
            return {"state": "off", "text": "자동 진단 꺼짐 — 저장 표 없음(migration 승인 대기)"}
        other = elsewhere(pid, store.release(rid))
        if other:   # D-168 (EV-034 CHG-171): shown like 'off', only the text differs
            return {"state": "elsewhere", "text": AUTO_ELSEWHERE.format(other)}
        if rid in diag.stopped:
            return {"state": "stopped", "reason": diag.stopped[rid],
                    "text": "✦ 자동 진단 멈춤 — 운영 기록을 읽을 수 없음 · 다음에 다시 합니다"}
        rows = autodiag.general(store.diagnoses(pid, rid))   # EV-065 CHG-263a: the band reads general rows (its check part from the wheel log)
        if not rows:
            return {"state": "none"}
        batch = rows[-1]["batch"]
        view = by_new_rule(pid, store.release(rid), [d for d in rows if d["batch"] == batch])   # EV-077 (가): rows from before shown by the new rule
        mine = [view.get(d["run_id"], d) for d in rows if d["batch"] == batch]
        mine = [d for d in mine if d and (d.get("rule_result") or {}).get("scope") != "procedure"]   # EV-077: targets only
        if not mine:   # D-468: a batch of procedure records only — no target, no band (max() over no row raised: the list answered 500)
            return {"state": "none"}
        n, k = len(mine), sum(1 for d in mine if d["status"] in ("done", "error"))
        if k < n:
            return {"state": "running", "k": k, "n": n,
                    "text": f"✦ 자동 진단 중 새 운영 Run {n}건 · {k} / {n} 끝 — 사람이 할 일은 없습니다. 끝나면 이 띠에 결과가 나옵니다"}
        fails = [d for d in mine if d.get("signal") == "fail"]
        fake = any(d.get("judge_kind") == "fake" for d in mine)
        suspects = [d["suspect_node"] for d in fails if d.get("suspect_node")]
        top = max(set(suspects), key=suspects.count) if suspects else None
        normal = [d for d in mine if d.get("signal") == "normal"]
        if fails:
            what = ("judge · " + top) if top and any(autodiag.cell(d)["text"].startswith("judge") for d in fails) else (top or "규칙 · 비율")
            text = f"✦ 자동 진단 끝 {n}건 중 {len(fails)}건 실패 신호({what})" if len(fails) < n else f"✦ 자동 진단 끝 {n}건 모두 실패 신호({what})"
        elif normal:
            text = f"✦ 자동 진단 끝 {n}건 — 정상 동작 {len(normal)}건"
        else:
            text = f"✦ 자동 진단 끝 {n}건 — 실패 신호 없음" + (" · judge(가짜)는 신호로 쓰지 않음" if fake else "")
        # EV-065 CHG-263a (D-412 decision 1, P-1): a diagnosis batch starts nothing — the general part gets no '→ 바퀴' tail; a person's
        # check that started or joined a wheel after the batch's last Run adds ' · 사람 체크 n건 → …' (n read from the latest such ① or
        # join line)
        end, wheel, checks = None, None, None
        last = max((d.get("finished_at") or 0) for d in mine)
        hits = [(lg["created_at"], i, lg, w) for w in store.wheels(pid) for i, lg in enumerate(store.wheel_logs(w["wheel_id"]))
                if lg["created_at"] >= last and CHECK_LINE.match(lg["text"]) and (lg["cell"] == 1 or lg["text"].endswith("한 번에 한 바퀴"))]
        if hits:
            _, _, lg, wheel = max(hits, key=lambda h: h[:2])
            end = "joined" if lg["text"].endswith("한 번에 한 바퀴") else "started"
            checks = sum(int(n) for n in CHECK_COUNT.findall(lg["text"].split(" — ", 1)[0]))
        if end == "started":
            text += f" · 사람 체크 {checks}건 → 개선 A-{wheel['seq']} 자동 시작"   # D-492 덧 18 ①: '자동 개선' on screen
        elif end == "joined":
            text += f" · 사람 체크 {checks}건 → 진행 중인 개선 A-{wheel['seq']} 기록에 더함"
        kinds = [d.get("judge_kind") for d in mine if d.get("judge_kind")]
        return {"state": "done", "n": n, "fails": len(fails), "suspect": top, "text": text, "wheel": wheel and {"seq": wheel["seq"], "end": end},   # AC-2 · AC-4
                "judge_fails": sum(1 for d in mine if autodiag.cell(d)["text"].startswith("judge ✗")),
                "judge_kind": max(set(kinds), key=kinds.count) if kinds else None}

    def auto_main_view(w):
        """EV-035: the wheel's sets (③) and the real calls it made — this wheel and this server's total against the cap (CHG-187)."""
        st = store.wheel_state(w["wheel_id"])
        sets = st.get("sets") or {}
        out = {"sets": {k: n for k in automain.DOC_SETS if (n := sum(1 for v in sets.values() if v == k)) or k != "excluded"} if sets else None, "doc": st.get("doc")}   # D-477 덧 5: 셈 밖 only when there is one
        if settings.mode == "real" and store.auto_main_ready:
            mine = store.spend(w["wheel_id"])
            out["spend"] = {"calls": mine["calls"], "wheel": round(mine["usd"], 2), "server": round(main.server_spent(), 2), "cap": settings.auto_budget_usd}
        return out

    # the ⑤ table's words come from the server, like the bar's cell names: no screen file carries '후보' (EV-030 Q-10)
    AUTO_ROW_LABELS = {"title": "3 ⑤ 후보 표", "note": "후보는 버전이 아니라 표의 줄 · 후보끼리만 비교 · 흔들리는 case 뺌 · 목표 1등(D-121 · D-122)",
                       "row": "후보 줄", "winner": "⑤ 표 1등만 새 버전 {name}이 됐습니다", "compare": "⑥ 비교 보기 ›"}

    def quality_view(q):
        """D-462 B (D 화면): the row's quality review — its findings (case · question · field · why · sure) with '에이전트' as the source,
        or the word for a review skipped or failed; None for a row it never reached."""
        if not q:
            return None
        return {"source": autoreview.SOURCE, "checked": q.get("checked"), "verdict": q.get("verdict"), "skipped": q.get("skipped"), "failed": q.get("failed"),
                "findings": [{"case": f["case"], "question": autoreview.LABEL[f["question"]], "field": f["field"], "why": f["why"], "sure": f["sure"]} for f in q.get("findings") or []]}

    def rec_shown(wid, rec):
        """D-659 덧: an efficiency recommendation as the screen reads it — '나아짐 n' counted again from the wheel's confirm Runs (the new
        version's, then the 운영 one started right after it) when it was kept before; the kept record is never written."""
        if (rec or {}).get("kind") != "efficiency":
            return rec
        w = store.wheel(wid) or {}
        conf = ((store.wheel_state(wid) or {}).get("suites") or {}).get("confirm")
        runs = [x for x in store.project(w["project_id"])["runs"] if (x.get("auto") or {}).get("role") == "confirm" and (x.get("auto") or {}).get("wheel") == w.get("seq")] if w else []
        k = max((i for i, x in enumerate(runs) if x["rel"] == (rec.get("new") or {}).get("release")), default=None)
        nb = next((x for x in runs[k + 1:] if x["rel"] == (rec.get("base") or {}).get("release")), None) if k is not None else None
        if not nb or not conf or not store.suite(conf):
            return rec
        return autocand.with_better(rec, store.run(nb["uid"]), store.run(runs[k]["uid"]), store.suite(conf)["cases"])

    def auto_row_view(r):
        """One row of the ⑤ table: its hypotheses, the cheap test (changed / predicted), the table columns, the result."""
        ct, tr = r.get("cheap_test") or {}, r.get("table_result") or {}
        col = tr.get("columns") or {}
        rel = store.release(r["release_id"]) if r.get("release_id") else None
        ret = col.get("retrieval")   # EV-043 CHG-211: a retriever row shows recall@10 in the fixed column and skips the cheap test
        crit = tr.get("criteria") or []
        quality_out = bool(crit) and crit[0].startswith(autoreview.DROP)   # D-462 B: the quality review's own line, not under '판정 기준 탈락'
        result = {"discarded": (r.get("check_result") or {}).get("why") or (f"버림 — 0 / {len(ct.get('cases') or [])} 바뀜" if "changed" in ct else "버림"),
                  "table_out": crit[0] if quality_out else "판정 기준 탈락 — " + ", ".join(crit), "winner": f"1등 → {(rel or {}).get('name', '새 버전')} ✦ 자동" + (f" · 권고 {VERDICT_WORD[r['recommendation']['decision']]}" if (r.get("recommendation") or {}).get("decision") in ("adopt", "hold") else ""),   # D-463 덧 31: hold too
                  "rec_reject": f"권고 버림 (자동) — {', '.join((r.get('recommendation') or {}).get('failed') or [])} · {(rel or {}).get('name', '새 버전')} 평가 중"}.get(r.get("outcome")) \
            or (f"{r['rank']}등" if r.get("rank") else "시험 중")
        return {"id": r["candidate_id"], "iteration": r["iteration"], "hypotheses": [f"H-{i}" for i in r["hypothesis_ids"]], "node": r["node"],
                "source": automain.MERGED_SOURCE if (r.get("check_result") or {}).get("merged") else None,   # D-470 ⓒ
                "cheap": f"{len(ct.get('changed') or [])} / {len(ct.get('cases') or [])} 바뀜" if "changed" in ct else f"건너뜀({ct['skipped']})" if ct.get("skipped")
                else ("시험 중" if ct.get("runs") else "—"), "checked": ct.get("checked") or [],   # D-525 덧 36: the checked cases its cheap test added
                "fix": ((f"예산 recall {autocand.frac(ret['base'].get('budget'))} → {autocand.frac(ret['row'].get('budget'))} · " if ret.get("by_budget") else "")   # D-492 덧 16
                        + f"recall@10 {autocand.frac(ret['base']['recall'])} → {autocand.frac(ret['row']['recall'])} · 기대값 일치 최종 {autocand.frac(ret['row']['final'])}"
                        f" · node {autocand.frac(ret['row']['nodes'])} · 오류 {col['errors']}"
                        if ret else f"{col['fixed']} / {col['fix']}" if col else "—"), "keep": (f"{col['kept']} / {col['keep']}" + (f" 망가짐 {len(col['broken'])}" if col.get("broken") else "")
                                                                         + (f" · {autocand.noise_text((tr.get('noise') or {}).get('cases'))}" if (tr.get("noise") or {}).get("cases") else "")) if col else "—",   # D-655
                "noise": autocand.noise_text((tr.get("noise") or {}).get("cases")) or None,
                "ref": autocand.ref_text(col) if col else "—",   # EV-089 (라): the strict count · rewrites, or '옛 엄격 셈 v1' on a row measured before
                "tokens": col.get("tokens"), "latency": col.get("latency"), "calls": col.get("calls"), "executions": r.get("executions"), "result": result, "outcome": r.get("outcome"),
                "efficiency": col.get("efficiency"),   # EV-069 CHG-EF-7 (P-4): cost · LLM calls · latency of an efficiency row
                "quality": quality_view(tr.get("quality")),
                "release": {"id": rel["id"], "name": rel["name"]} if rel else None,
                "recommendation": {**{k: (rec_shown(r["wheel_id"], r.get("recommendation")) or {}).get(k) for k in ("decision", "failed", "reasons", "confirm")},
                                   "scoring": autocand.scoring_word(r["recommendation"])} if r.get("recommendation") else None}

    @app.get("/api/projects/{pid}/releases/{rid}/fixed-flow")
    def fixed_flow_view(pid: str, rid: str):
        """EV-069 CHG-EF-1 (EV-030 CHG-160 C2): the version's fixed flow — read now when it has none for its revision."""
        need_project(pid)
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid or not rel.get("pack"):
            raise HTTPException(status_code=404, detail="Agent Pack 버전이 없습니다")
        if not store.auto_ready:
            raise HTTPException(status_code=409, detail=AUTO_OFF)
        return fixedflow.view(ff_row(rel))

    @app.post("/api/projects/{pid}/releases/{rid}/fixed-flow/confirm", status_code=201)
    def fixed_flow_confirm(pid: str, rid: str, body: Confirm):
        """EV-069 CHG-EF-1: '확인' with an alias — once per revision; an unreadable list cannot be confirmed; the list is never edited."""
        why = deploying.alias_error(body.by)
        if why:
            raise HTTPException(status_code=422, detail=why)
        row = fixed_flow_view(pid, rid)
        if row["state"] == fixedflow.STATE_UNREADABLE:
            raise HTTPException(status_code=409, detail=f"읽지 못한 목록은 확인할 수 없습니다 — {row['why']}")
        if row["state"] == fixedflow.STATE_DONE:
            raise HTTPException(status_code=409, detail=f"이미 확인함({row['confirmed_by']})")
        return fixedflow.view(store.confirm_fixed_flow(rid, body.by))

    @app.post("/api/projects/{pid}/auto/efficiency", status_code=201)
    def start_efficiency(pid: str, body: StartEfficiency):
        """EV-069 CHG-EF-2 (P-1): '효율 개선 시작' on the Project's 운영 version — one wheel at a time (D-97)."""
        need_project(pid)
        if not store.auto_ready:
            raise HTTPException(status_code=409, detail=AUTO_OFF)
        why = deploying.alias_error(body.by)
        if why:
            raise HTTPException(status_code=422, detail=why)
        op = operational_of(pid, store.releases(pid), store.deployments())[0]
        rel = store.release(op) if op else None
        if not rel or not rel.get("pack"):
            raise HTTPException(status_code=409, detail="운영 버전 없음")
        key_stop(rel, main.settings.mode)   # D-492 덧 24: the wheel's Runs would start without the key file
        w = store.open_wheel(pid)
        if w:
            raise HTTPException(status_code=409, detail={"message": f"열린 개선 A-{w['seq']}", "wheel": f"A-{w['seq']}"})
        work = body.work_suite or autocycle.default_work_suite(store, rel)
        refused = autocycle.efficiency_refusal(store, rel, body.goals, work, body.confirm_suite)
        if refused:
            raise HTTPException(status_code=refused[0], detail=refused[1])
        _, w = app.state.autodiag.wheels.start_efficiency(pid, rel, f"사람({body.by})", body.goals, work, body.confirm_suite)
        return {"wheel": f"A-{w['seq']}", "kind": w["kind"]}

    @app.post("/api/projects/{pid}/auto/{name}/stop", status_code=202)
    def stop_wheel(pid: str, name: str, body: StopWheel):
        """D-476 덧 20 (packrt E1 d476-wheel-stop): a person asks one wheel to stop — the request and a log line only (no long lock: the
        worker's pass is never waited for); the worker ends the wheel before its next step (automain.Main.stop_wheel)."""
        need_project(pid)
        if not store.auto_ready:
            raise HTTPException(status_code=409, detail=AUTO_OFF)
        w = next((x for x in store.wheels(pid) if f"A-{x['seq']}" == name), None)
        if not w:
            raise HTTPException(status_code=404, detail="자동 개선이 없습니다")
        why = deploying.alias_error(body.by)
        if why:
            raise HTTPException(status_code=422, detail=why)
        if len(body.reason or "") > 200:
            raise HTTPException(status_code=422, detail="까닭은 200자 안으로 적습니다")
        if w["ended_at"] is not None:
            raise HTTPException(status_code=409, detail=f"이미 끝남 — {w['outcome'] or ''}".rstrip(" —"))
        req, new = store.request_wheel_stop(w["wheel_id"], body.by, body.reason.strip())
        if new:
            store.wheel_log(w["wheel_id"], w["cell"] or 3, automain.STOP_ASKED.format(by=body.by))
            app.state.autodiag.kick()
        return {"wheel": name, "requested_at": req["at"]}

    def quality_wait_row(w):
        """D-652 다섯째: the row an efficiency wheel ended on for a person (the quality review below confidence 0.7, '사람 확인 필요') and
        not decided yet — else None."""
        if w.get("ended_at") is None or w.get("kind") != "efficiency":
            return None
        cid = ((store.wheel_state(w["wheel_id"]) or {}).get("cand") or {}).get("winner")
        row = next((r for r in store.candidates(w["wheel_id"]) if r["candidate_id"] == cid), None) if cid else None
        q = ((row or {}).get("table_result") or {}).get("quality") or {}
        if not row or w.get("outcome") != autoreview.HUMAN.format(row=cid) or q.get("verdict") != "human" or q.get("human"):
            return None
        return row

    @app.post("/api/projects/{pid}/auto/{name}/quality")
    def quality_human(pid: str, name: str, body: QualityHuman):
        """D-652 다섯째 (llmops-2 238 · 248): a person passes or drops the row the quality review held for them — alias and reason on the
        row and in the log; the wheel opens again and its worker goes on (pass: the version · ⑥ · ⑦ as before; drop: the next row). Once."""
        need_project(pid)
        if not store.auto_ready:
            raise HTTPException(status_code=409, detail=AUTO_OFF)
        w = next((x for x in store.wheels(pid) if f"A-{x['seq']}" == name), None)
        if not w:
            raise HTTPException(status_code=404, detail="자동 개선이 없습니다")
        if body.decision not in ("pass", "drop"):
            raise HTTPException(status_code=422, detail="통과(pass) 또는 버림(drop)만 받습니다")
        why = deploying.alias_error(body.by)
        if why:
            raise HTTPException(status_code=422, detail=why)
        reason = (body.reason or "").strip()
        if not reason or len(reason) > 200:
            raise HTTPException(status_code=422, detail="까닭을 200자 안으로 적어야 남습니다")
        row = quality_wait_row(w)
        if not row:
            raise HTTPException(status_code=409, detail="사람 확인을 기다리는 품질 검토 줄이 없습니다")
        other = store.open_wheel(pid)
        if other and other["wheel_id"] != w["wheel_id"]:
            raise HTTPException(status_code=409, detail={"message": f"열린 개선 A-{other['seq']}", "wheel": f"A-{other['seq']}"})
        tr = row["table_result"]
        tr["quality"] = {**tr["quality"], "human": {"decision": body.decision, "by": body.by, "reason": reason, "at": time.strftime("%m-%d %H:%M:%S")}}
        store.candidate_update(row["candidate_id"], table_result=tr)
        store.wheel_log(w["wheel_id"], 5, autoreview.HUMAN_SAID.format(row=row["candidate_id"], word="통과" if body.decision == "pass" else "버림", by=body.by, reason=reason))
        store.wheel_update(w["wheel_id"], ended_at=None, outcome=None, cell_state="running")
        app.state.autodiag.kick()
        return {"wheel": name, "row": row["candidate_id"], "decision": body.decision}

    @app.get("/api/projects/{pid}/auto/settings")
    def auto_settings_view(pid: str):
        """EV-069 CHG-EF-2 (P-2): the efficiency row's set time (off by default) · work · confirm suite; the goals are fixed (Q-EF-8)."""
        need_project(pid)
        eff = (store.auto_settings(pid) if store.auto_ready else {}).get("efficiency") or {}
        return {"efficiency": {"schedule": eff.get("schedule"), "work_suite": eff.get("work_suite"), "confirm_suite": eff.get("confirm_suite"),
                               "goals": list(autocycle.GOAL_ORDER), "scope": autocycle.EFF_SCOPE}}

    @app.put("/api/projects/{pid}/auto/settings")
    def auto_settings_save(pid: str, body: AutoSettings):
        need_project(pid)
        if not store.auto_ready:
            raise HTTPException(status_code=409, detail=AUTO_OFF)
        why = deploying.alias_error(body.by)
        if why:
            raise HTTPException(status_code=422, detail=why)
        e = body.efficiency or {}
        sched = e.get("schedule")
        if sched is not None and (not isinstance(sched, dict) or sched.get("day") not in autocycle.DAYS or not autocycle.valid_hhmm(sched.get("time"))):
            raise HTTPException(status_code=422, detail="정한 시각은 요일(mon ~ sun) · HH:MM")
        for k in ("work_suite", "confirm_suite"):
            if e.get(k) and not store.suite(e[k]):
                raise HTTPException(status_code=422, detail=f"suite 없음({k})")
        data = store.auto_settings(pid)
        old = data.get("efficiency") or {}
        # the slot that has already come counts as used: a new or changed time opens a wheel from its next slot, not right away
        data["efficiency"] = {"schedule": sched, "work_suite": e.get("work_suite"), "confirm_suite": e.get("confirm_suite"),
                              "last_slot": autocycle.slot_of(sched["day"], sched["time"], time.time()) if sched else old.get("last_slot")}
        store.set_auto_settings(pid, data, body.by)
        return auto_settings_view(pid)

    @app.get("/api/projects/{pid}/auto/{name}")
    def auto_wheel_detail(pid: str, name: str):
        """EV-035 CHG-189 (supplement A6 · 09/26, B1 · B2 · 15/16): one wheel — its bar and log (as in the list), the base version's graph with
        the signal node (분홍) and the responsible node (보라), the responsible-node table, the hypotheses table by iteration and batch."""
        listing = auto_wheels(pid)
        w = next((x for x in listing.get("wheels", []) if x["name"] == name), None)
        if not w:
            raise HTTPException(status_code=404, detail="자동 개선이 없습니다")
        st = store.wheel_state(w["id"])
        rel = store.release(w["base"]["release"]) or {}
        resp = st.get("resp") or {}
        qw = quality_wait_row(store.wheel(w["id"]) or {})   # D-652 다섯째
        rows = [auto_row_view(r) for r in store.candidates(w["id"])]
        eff = w.get("kind") == "efficiency"   # EV-069 (slice review 2 B3, preview P-4): the fixed-flow column and the base values
        eff_ff, eff_base = {}, None
        if eff:
            for r in store.candidates(w["id"]):
                cr, hid = r.get("check_result") or {}, (r.get("hypothesis_ids") or [None])[0]
                eff_ff[hid] = ("어김 — " + cr["fixed_flow"][0].get("message", "")) if cr.get("fixed_flow") else "통과" if r.get("revision") else "—"
            base_runs = [store.run(u) for u in st.get("baseline") or []]
            if base_runs and all(base_runs):
                eff_base = autocand.eff_mean([autocand.eff_measures(x) for x in base_runs])
        alone = {int(r["hypotheses"][0][2:]): r for r in rows if len(r["hypotheses"]) == 1}   # the row that tried one hypothesis by itself
        hyps = [{"id": h["hypothesis_id"], "iteration": h["iteration"], "batch": h["batch"], "batch_no": h["batch_no"], "node": h["node"], "knob": h["knob"],
                 "value": h["value"], "predicts": (h["detail"] or {}).get("predicts") or [], "why": (h["detail"] or {}).get("why"),
                 "result": alone[h["hypothesis_id"]]["result"] if h["hypothesis_id"] in alone else h["result"],   # FINAL-B1: the row's result once it was tried
                 "cheap": alone[h["hypothesis_id"]]["cheap"] if h["hypothesis_id"] in alone else None,
                 "checked": alone[h["hypothesis_id"]]["checked"] if h["hypothesis_id"] in alone else [],   # D-525 덧 36
                 "discarded": h["discarded_reason"], "source": h["source"], **({"fixed_flow": eff_ff.get(h["hypothesis_id"])} if eff else {}),
                 **({"option": (h["detail"] or {})["option"]} if (h["detail"] or {}).get("option") else {}),   # D-476
                 **({"merge": (h["detail"] or {})["merge"]} if (h["detail"] or {}).get("merge") else {}),   # A-28
                 "cells": hyp_cells(h)} for h in store.hypotheses(w["id"])]   # D-549 덧 3: 대상 · 바꿈 · 근거
        return {**w, "cells_names": listing.get("cells"), "graph": rel.get("graph"), "release": {"id": rel.get("id"), "name": rel.get("name")},
                "responsible": {"node": resp.get("node"), "signal": resp.get("signal"), "decided": resp.get("decided"), "basis": resp.get("basis"), "evidence": resp.get("evidence"), "probe": resp.get("probe"),   # D-549 덧 3 A
                                "rows": automain.shown_rows(resp)["rows"],   # D-525 덧 30 · D-652 9-a: a case that points to the concluded node
                                "case": (resp.get("shown") or {}).get("case"), "cases": len(resp.get("per_case") or [])} if resp else None,
                "diagnosis": automain.diagnosis_of(resp) if resp else None,   # D-549 덧 3: target kind + target + evidence
                "hypotheses": hyps, "rows": rows, "labels": AUTO_ROW_LABELS, **({"quality_wait": {"row": qw["candidate_id"]}} if qw else {}), **({"eff_base": eff_base} if eff else {}),
                "changed": sorted({r["node"] for r in store.candidates(w["id"]) if r.get("release_id")}),   # the nodes the version rows changed (주황)
                "compare": f"#/p/{pid}/compare/{name}" if any(r.get("recommendation") for r in store.candidates(w["id"])) else None, "settings": {k: v for k, v in (json.loads(store.wheel(w["id"])["settings"]) or {}).items() if not k.startswith("_")}}

    @app.get("/api/projects/{pid}/auto")
    def auto_wheels(pid: str):
        """EV-030 CHG-156 (supplement A5 · 08/26): the Project's improvement wheels, newest first — the 8-cell bar, where each one is,
        its progress log, and the right-hand facts. Off when the store has no auto tables."""
        need_project(pid)
        if not store.auto_ready:
            return {"state": "off", "wheels": []}
        other = project_elsewhere(pid)
        if other:   # D-168
            return {"state": "elsewhere", "text": AUTO_ELSEWHERE.format(other), "wheels": []}
        out = []
        for w in store.wheels(pid):
            base = store.release(w["base_release_id"]) or {}
            out.append({"id": w["wheel_id"], "name": f"A-{w['seq']}", "seq": w["seq"], "kind": w["kind"], "started_by": w["started_by"],
                        "base": {"release": w["base_release_id"], "name": base.get("name"), "revision": w["base_revision"]},
                        "scope": w["scope"], "goal": w["goal"], "cell": w["cell"], "cell_state": w["cell_state"], "cells": autocycle.cells(w), "iteration": w["iteration"],
                        "status": autocycle.status_text(w), "outcome": w["outcome"], "started_at": w["started_at"], "ended_at": w["ended_at"],
                        "stop": (json.loads(w.get("settings") or "{}") or {}).get("_stop"),   # D-476 덧 20: a person's stop request
                        "log": [{"cell": lg["cell"], "text": lg["text"], "at": lg["created_at"]} for lg in store.wheel_logs(w["wheel_id"])],
                        **auto_main_view(w)})
            out[-1]["far"] = autocycle.far_text(w, out[-1]["log"], store.candidates(w["wheel_id"]))   # D-659 ③
        return {"state": "on", "cells": list(autocycle.CELLS), "wheels": out, "efficiency": efficiency_head(pid)}

    def efficiency_head(pid):
        """EV-069 CHG-EF-7 (P-1): what the '효율 개선 시작' button needs — the 운영 version, its fixed flow state, the open wheel."""
        op = operational_of(pid, store.releases(pid), store.deployments())[0]
        rel = store.release(op) if op else None
        w = store.open_wheel(pid)
        row = store.fixed_flow(rel["id"]) if rel and rel.get("pack") else None   # read only: the list is written when a person opens it
        state = fixedflow.state(row) if row and row["pack_revision"] == rel["pack"]["revision"] else fixedflow.STATE_NEED
        return {"operational": {"id": rel["id"], "name": rel["name"], "revision": rel["pack"]["revision"]} if rel and rel.get("pack") else None,
                "fixed_flow": state if rel and rel.get("pack") else None, "open": f"A-{w['seq']}" if w else None,
                "work_suite": autocycle.default_work_suite(store, rel) if rel else None}

    def check_paragraph(pid, rid, run_id):
        """EV-065 CHG-263a: the '사람 체크' paragraph of the detail — one line per person's check of this Run (its check row): kind,
        source, time, what the diagnosis of that row found and the improvement that took it (the wheel open when it finished)."""
        wheels = store.wheels(pid)
        out = []
        for d in store.diagnoses(pid, rid):
            if not autodiag.is_check(d) or d["run_id"] != run_id or d["rubric_version"].endswith(autodiag.SOLVED_KEY):
                continue
            ck = (d.get("ratio_result") or {}).get("check") or {}
            at = d.get("finished_at") or d["created_at"]
            took = [w for w in wheels if w["started_at"] <= at + 1 and (w["ended_at"] is None or w["ended_at"] >= at)] if d["status"] == "done" else []
            out.append({"kind": ck.get("kind") or autodiag.LABEL_KIND, "source": "LLMOps 라벨" if ck.get("label") or "label-" in d["rubric_version"] else "앱 기록",
                        "node": ck.get("node"), "at": at, "status": d["status"], "judge": autodiag.cell(d)["text"] if d["status"] == "done" else None,
                        "wheel": f"A-{took[-1]['seq']}" if took else None})
        return out

    def offer_of(run_id):
        """D-633 (app 001-zzz114): the exception recommendation of the person's check record about this Run — the last record whose
        offer names the Run or that names it among its Runs — {relation_id, run_id, adopted, picked_section, how, record_key, recorded_at};
        None when there is none or the records cannot be read now."""
        try:
            recs = opsruns.reader(settings).check_records() or []
        except Exception:   # noqa: BLE001 - the 운영 기록 unreadable: no line this time (the paragraph says nothing more)
            return None
        got = None
        for rec in sorted(recs, key=lambda r: str(r.get("recorded_at") or "")):
            o = opsruns.exception_offer({"exception_offer": rec.get("exception_offer")})
            if o and (o["run_id"] == run_id or run_id in (rec.get("runs") or [])):
                got = {**o, "record_key": rec.get("record_key"), "recorded_at": rec.get("recorded_at")}
        return got

    @app.get("/api/projects/{pid}/releases/{rid}/ops-runs/{run_id}/diagnosis")
    def ops_run_diagnosis(pid: str, rid: str, run_id: str):
        """One operational Run's diagnosis (the right-hand '진단 결과' box and the 피드백 · 검토 tab, CHG-152 · 154). Results only (Q-11)."""
        rel, _ = ops_target(pid, rid)
        if not store.auto_ready:
            return {"state": "off"}
        if elsewhere(pid, rel):   # D-168: rows written before stay unshown
            return {"state": "elsewhere"}
        rows = [d for d in autodiag.general(store.diagnoses(pid, rid)) if d["run_id"] == run_id]
        lbs = [lb for lb in store.labels(pid, rid) if lb["run_id"] == run_id]
        label = lbs[-1] if lbs else None
        lists = {"labels": list(autodiag.LABELS), "label_names": {v: autodiag.label_word(v) for v in autodiag.LABELS}, "label_list_version": autodiag.LABEL_LIST_VERSION,
                 "label": {k: label[k] for k in ("label", "node", "by_alias", "reason", "created_at", "label_list_version")} if label else None}
        check = diag.check_kinds(pid, rel).get(run_id)
        lists["checks"] = check_paragraph(pid, rid, run_id)
        lists["offer"] = offer_of(run_id)   # D-633: the exception recommendation seen in the check and whether it was picked
        # EV-065 (PRES A3 · A4): the judge of the Run's latest finished check row — the pink node and the judge table read it first
        cks = [d for d in store.diagnoses(pid, rid) if autodiag.is_check(d) and d["run_id"] == run_id and d["status"] == "done" and d.get("judge_kind")]
        lists["check_judge"] = ({"judge_kind": cks[-1]["judge_kind"], "judge": cks[-1]["judge_result"], "suspect_node": cks[-1]["suspect_node"],
                                 "signal": cks[-1]["signal"], "rubric": (cks[-1]["judge_result"] or {}).get("rubric")} if cks else None)
        if not rows:
            return {"state": "none", "cell": autodiag.cell(None, label, check), **lists}
        d = rows[-1]
        if autodiag.before_targets(d):   # EV-077 (가): a row from before, shown by the new rule (read only)
            d = by_new_rule(pid, rel, [x for x in autodiag.general(store.diagnoses(pid, rid)) if x["batch"] == d["batch"]]).get(run_id, d)
        if d is None or (d.get("rule_result") or {}).get("scope") == "procedure":   # a procedure record: no diagnosis shown
            return {"state": "none", "procedure": True, "cell": autodiag.cell(None, label, check), **lists}
        return {"state": d["status"], "cell": autodiag.cell(d, label, check), "signal": d.get("signal"), "suspect_node": d.get("suspect_node"),
                "rule": d.get("rule_result"), "ratio": d.get("ratio_result"), "judge": d.get("judge_result"), "judge_kind": d.get("judge_kind"),
                "rubric": d.get("rubric_version"), "error_kind": d.get("error_kind"), "batch": d["batch"], **lists}

    @app.post("/api/projects/{pid}/releases/{rid}/ops-runs/{run_id}/labels", status_code=201)
    def add_ops_label(pid: str, rid: str, run_id: str, body: NewLabel):
        """EV-030 CHG-154 (supplement A4 ②): a person's label on one operational Run of this version — from the fixed list, with an
        alias and a reason; append only, the last one counts and comes before the diagnosis cell. The reason is masked against the
        Run's raw values before it is stored (Q-11)."""
        rel, ids = ops_target(pid, rid)
        if not store.auto_ready:
            raise HTTPException(status_code=409, detail="자동 진단 꺼짐 — 저장 표 없음(migration 승인 대기)")
        other = elsewhere(pid, rel)
        if other:   # D-168
            raise HTTPException(status_code=409, detail=AUTO_ELSEWHERE.format(other))
        by, reason = body.by.strip(), body.reason.strip()
        if not by or not reason:
            raise HTTPException(status_code=422, detail="남긴 사람 별명과 이유를 적으세요")
        if body.label not in autodiag.LABELS:
            raise HTTPException(status_code=422, detail="라벨은 목록에서 고르세요")
        try:
            r = opsruns.reader(settings)
            d = r.detail(run_id, rel.get("graph"), ops_summary(rel)) if re.fullmatch(r"run_[0-9A-HJKMNP-TV-Z]{26}", run_id) else None
            raw = autodiag.run_raw(r, run_id, rel.get("graph"), d) if d else []
        except opsruns.OpsUnavailable as e:
            raise HTTPException(status_code=503, detail=f"운영 기록을 읽을 수 없습니다 — {opsruns.REASON.get(e.kind, e.kind)}")
        if not d or d["deployment_id"] not in ids or d["pack_revision"] != rel["pack"]["revision"]:
            raise HTTPException(status_code=404, detail="이 버전의 운영 Run이 아닙니다")
        node = body.node if body.node and any(v["node"] == body.node for v in d["visits"]) else None
        store.add_label(pid, rid, run_id, node, body.label, autodiag.LABEL_LIST_VERSION, by, autodiag.mask_raw(reason, raw))
        app.state.autodiag.kick()   # EV-064 CHG-CF-5: a ✗ label starts the wheel on the next pass, now
        return ops_run_diagnosis(pid, rid, run_id)

    @app.post("/api/projects/{pid}/releases/{rid}/ops-runs/{run_id}/promote", status_code=201)
    def promote_ops_run(pid: str, rid: str, run_id: str, body: NewPromotion):
        """EV-025 OPS-3 CHG-125 (manual p.30 ③): put one operational Run of this version up as an evaluation case — its stored input
        (the deploy side's redacted payload, sha-checked) and what was observed; append only, no expected value (Q-2 A)."""
        return promote_run(pid, rid, run_id, body.by, body.reason)

    def promote_run(pid, rid, run_id, by, reason):
        """The one way a Run goes up (a person's button and, EV-035 CHG-180, the wheel's ③ by '자동(바퀴 A-n)')."""
        rel, ids = ops_target(pid, rid)
        if not re.fullmatch(r"run_[0-9A-HJKMNP-TV-Z]{26}", run_id):   # an evaluation Run (R-n) is already a suite input
            raise HTTPException(status_code=422, detail="운영 Run만 올릴 수 있습니다")
        by, reason = by.strip(), reason.strip()
        if not by or not reason:
            raise HTTPException(status_code=422, detail="올린 사람 별명과 이유를 적으세요")
        try:
            r = opsruns.reader(settings)
            d = r.detail(run_id, rel.get("graph"), ops_summary(rel))
            if d and d["deployment_id"] in ids and d["pack_revision"] == rel["pack"]["revision"]:
                ref = d.get("input_ref")
                value, why = opsruns.read_payload(r.root, ref, r.trace.expired([ref.split(":", 1)[1]] if ref and ":" in ref else []))
        except opsruns.OpsUnavailable as e:
            raise HTTPException(status_code=503, detail=f"운영 기록을 읽을 수 없습니다 — {opsruns.REASON.get(e.kind, e.kind)}")
        if not d or d["deployment_id"] not in ids or d["pack_revision"] != rel["pack"]["revision"]:
            raise HTTPException(status_code=404, detail="이 버전의 운영 Run이 아닙니다")
        if value is None:
            raise HTTPException(status_code=422, detail=f"원문 없음({why}) — 올릴 입력이 없습니다")
        observed = {k: d.get(k) for k in ("result", "result_fields", "status", "outcome", "input", "started_at")}
        try:
            return store.add_promotion(pid, rid, run_id, d.get("deployment_id"), d["pack_revision"], ref, value, observed, by, reason)
        except INTEGRITY_ERRORS:
            raise HTTPException(status_code=409, detail="이미 올린 Run입니다")

    @app.get("/api/projects/{pid}/promotions")
    def list_promotions(pid: str):
        need_project(pid)
        return {"promotions": store.promotions(pid)}

    @app.post("/api/projects/{pid}/eval-suites/promoted", status_code=201)
    def add_promoted_suite(pid: str, body: PromotedSuite):
        """EV-025 OPS-3 CHG-126 (Q-1 A): a new suite = a registered suite S-k's copy + the chosen 올린 case at the end of its dataset,
        registered by the usual rules (same content → 409, copy hash check). S-k and its copy stay as they are (PRES-126)."""
        need_project(pid)
        base = store.suite(body.suite)
        src = suites.copy_dir(settings.data_dir, base["id"]) if base and base["project_id"] == pid else None
        if not base or not src or not src.is_dir():
            raise HTTPException(status_code=422, detail="기준 평가 suite가 없습니다")
        have = {x["promotion_id"]: x for x in store.promotions(pid)}
        chosen = [have.get(i) for i in dict.fromkeys(body.promotions)]
        if not all(chosen):
            raise HTTPException(status_code=422, detail="이 Project의 올린 case가 아닙니다")
        rels = [store.release(x["release_id"]) for x in chosen]
        if not all(r and r.get("pack") and r["pack"].get("id") == base["pack_id"] for r in rels):
            raise HTTPException(status_code=422, detail=f"올린 case의 Pack이 기준 suite의 Pack({base['pack_id']})과 다릅니다")
        rows = [{"case_id": f"op-{x['source_run_id']}", "input": x["input"]} for x in chosen]
        stage = settings.data_dir / "staging" / f"promo-{uuid.uuid4().hex[:8]}"
        try:
            suites.with_cases(src, stage, base["folder"], rows)
            review_nodes = [n["name"] for n in (rels[0].get("graph") or {}).get("nodes", []) if str(n.get("kind", "")).startswith("human_review")]
            su = suites.read_suite(stage, base["folder"], rels[0]["pack"], review_nodes)
            su.pop("source")
            su["derived_from"] = {"suite": base["id"], "promotions": [x["promotion_id"] for x in chosen]}
            return register_suite(pid, su, stage / "evals" / base["folder"])
        except (suites.SuiteError, OSError) as e:
            raise HTTPException(status_code=422, detail=f"평가 suite를 등록할 수 없습니다 — {e}")
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    @app.post("/api/runs/{uid}/cancel")
    def cancel_run(uid: str):
        r = need_run(uid)
        if r["status"] == "실행 중":
            executor.cancel(uid)
        return {"ok": True}

    BRIEF_KEYS = ("purpose", "change", "io", "eval", "forbid", "fixture", "trace")

    @app.post("/api/projects/{pid}/briefs", status_code=201)
    def add_brief(pid: str, body: Brief):
        """Implementation Brief: stored and shown only; saving makes no release, no code file and runs nothing (AC-22)."""
        need_project(pid)
        return store.save_brief(pid, body.title.strip() or "제목 없는 요청서", {k: body.fields.get(k, "") for k in BRIEF_KEYS})

    @app.put("/api/projects/{pid}/briefs/{bid}")
    def update_brief(pid: str, bid: str, body: Brief):
        need_project(pid)
        b = store.brief(bid)
        if not b or b["project_id"] != pid:
            raise HTTPException(status_code=404, detail="요청서가 없습니다")
        return store.save_brief(pid, body.title.strip() or "제목 없는 요청서", {k: body.fields.get(k, "") for k in BRIEF_KEYS}, bid)

    uploads_root = (settings.data_dir / "uploads").resolve()

    def upload_copy_dir(rel):
        """The folder that a delete may remove: only an upload copy inside <data dir>/uploads. Any other path (a local-path
        original, a built-in example, or a record pointing outside the data dir) is never touched."""
        if rel["how"] != HOW_LABEL["upload"]:
            return None
        path = Path(rel["path"]).resolve()
        return path if path.parent == uploads_root and path.name.startswith("u-") else None

    def stop_runs(run_ids):
        """Runs still going are cancelled and waited for before their records are removed."""
        for uid in run_ids:
            if store.run(uid) and store.run(uid)["status"] == "실행 중":
                executor.cancel(uid)
        for uid in run_ids:
            executor.wait(uid, timeout=60)

    def remove_copies(releases):
        removed, kept, failed = [], [], []
        for rel in releases:
            if rel["sourceKind"] == "agent_pack":   # the Pack copy under <data dir>/packs/<id>; the work folder is never touched
                kept_revs = packs.revisions_dir(settings.data_dir, rel["id"])
                if kept_revs and kept_revs.is_dir():
                    shutil.rmtree(kept_revs, ignore_errors=True)
                d = packs.copy_dir(settings.data_dir, rel["id"])
                if d:
                    shutil.rmtree(d, ignore_errors=True)
                    if d.exists():
                        failed.append(str(d))
                continue
            d = upload_copy_dir(rel)
            if d:
                shutil.rmtree(d, ignore_errors=True)
                (failed if d.exists() else removed).append(str(d) if d.exists() else rel["id"])  # reported as it actually is
            elif rel["how"] == HOW_LABEL["upload"]:
                kept.append(rel["path"])  # an upload record pointing outside <data dir>/uploads: refused, nothing deleted there
        return removed, kept, failed

    def counts_view(c):
        return {k: v for k, v in c.items() if k not in ("run_ids", "releases", "suite_ids")}

    @app.get("/api/projects/{pid}/delete-summary")
    def project_delete_summary(pid: str):
        need_project(pid)
        return counts_view(store.delete_counts(pid=pid))

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        """Irreversible (P-12): the Project's records and its upload copies; local-path originals stay as they are."""
        need_project(pid)
        with life:
            deleting.add(pid)  # no new Run starts in this Project while it is being deleted
        try:
            c = store.delete_counts(pid=pid)
            stop_runs(c["run_ids"])
            store.delete_project(pid)
        finally:
            deleting.discard(pid)
        removed, kept, failed = remove_copies(c["releases"])
        for sid in c.get("suite_ids", []):   # the Project's evaluation suite copies go with it (EV-002 CHG-11)
            d = suites.copy_dir(settings.data_dir, sid)
            if d:
                shutil.rmtree(d, ignore_errors=True)
                if d.exists():
                    failed.append(str(d))
        return {"deleted": counts_view(c), "upload_copies_removed": len(removed), "paths_not_deleted": kept, "copies_not_removed": failed}

    @app.get("/api/projects/{pid}/releases/{rid}/delete-summary")
    def release_delete_summary(pid: str, rid: str):
        need_project(pid)
        r = store.release(rid)
        if not r or r["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        return counts_view(store.delete_counts(rid=rid))

    @app.delete("/api/projects/{pid}/releases/{rid}")
    def delete_release(pid: str, rid: str):
        need_project(pid)
        r = store.release(rid)
        if not r or r["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        with life:
            deleting.add(rid)
        try:
            c = store.delete_counts(rid=rid)
            stop_runs(c["run_ids"])
            store.delete_release(rid)
        finally:
            deleting.discard(rid)
        removed, kept, failed = remove_copies(c["releases"])
        return {"deleted": counts_view(c), "upload_copies_removed": len(removed), "paths_not_deleted": kept, "copies_not_removed": failed}

    @app.post("/api/projects/{pid}/input-sets", status_code=201)
    def add_input_set(pid: str, body: NewInputSet):
        """A saved input set from a file (input only) or lines written on screen. Nothing is saved on an error (AC-02)."""
        need_project(pid)
        if body.how == "file":
            try:
                cases = parse_file(body.name, body.text, need_expected=False)
            except ParseError as e:
                raise HTTPException(status_code=422, detail=str(e))
            name = body.name
        else:
            cases = [l.strip() for l in body.text.splitlines() if l.strip()]
            if not cases:
                raise HTTPException(status_code=422, detail="입력값을 한 줄에 하나씩 적으세요")
            name = f"직접 쓴 입력 · {time.strftime('%m-%d %H:%M:%S')}"
        return store.add_input_set(pid, name, "파일" if body.how == "file" else "직접", cases)

    @app.get("/api/projects/{pid}/releases/{rid}/suites-available")
    def suites_available(pid: str, rid: str):
        """evals/<suite>/ folders of a Pack release's work folder (read only)."""
        need_project(pid)
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid or not rel.get("pack"):
            raise HTTPException(status_code=422, detail="Agent Pack release를 고르세요")
        return {"suites": suites.available(rel["pack"]["work_folder"])}

    def run_copy(rel, run=None):
        """The Pack copy a Run used: the release copy, or for a Run of an earlier revision that revision's kept copy (S-2 · S-14)."""
        copy = rel["pack"].get("copy", "")
        if run and run.get("code_hash") and run["code_hash"] != rel["code_hash"]:
            kept = next((x for x in rel["pack"].get("revisions") or [] if x.get("code_hash") == run["code_hash"]), None)
            if kept and Path(kept["copy"]).is_dir():
                copy = kept["copy"]
        return copy

    @app.get("/api/projects/{pid}/releases/{rid}/nodes/{node_id}")
    def node_detail_view(pid: str, rid: str, node_id: str, run: str | None = None, case: int = 0, iter_: int = Query(1, alias="iter"), edit: int = 0,
                         ops_run: str | None = None):
        """Node detail of an Agent Pack release (EV-016 CHG-81). Read only: the release copy's pack.yaml and, with `run`, that
        Run's stored visits of one case (input = reads keys only, output = writes keys only). With `edit=1` and no `run`, while
        this release has the open edit, the edited Pack is read (a temporary copy, nothing stored) — the popover's form shows the
        values the person already applied, and a node only in the edit has a detail (EV-022 manual p.9 · 13, api-contract v1.9)."""
        need_project(pid)
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        if rel["sourceKind"] != "agent_pack":
            raise HTTPException(status_code=422, detail="Agent Pack release만 node 상세가 있습니다")
        e = store.open_edit(pid) if edit and not run else None
        if e and e["release"] == rid:
            try:
                folder, cleanup = editing.temp_copy(e["base_dir"], e["lines"], pending_ok=True)   # a waiting node has its popover (p.14)
            except ValueError as err:   # an edit that does not pass yet
                raise HTTPException(status_code=409, detail={"kind": "edit-not-ready", "reasons": [x.get("message") for x in err.args[0]]})
            try:
                detail = node_detail_from(rel, str(folder), node_id, None, case, iter_, tolerate=editing.waiting_message)
            finally:
                cleanup()
            detail["revision_read"] = "edit"
            return detail
        r0 = store.run(run) if run else None
        copy = run_copy(rel, r0 if r0 and r0["rel"] == rid else None)
        detail = node_detail_from(rel, copy, node_id, run, case, iter_)
        detail["revision_read"] = "earlier" if copy != rel["pack"].get("copy") else "current"
        if ops_run:   # EV-025 OPS-2 (manual p.30 ②): the node in one operational Run — reads keys in, writes keys out, as run_value
            detail["ops_value"] = ops_node_value(pid, rel, detail, ops_run, iter_)
        return detail

    def ops_node_value(pid, rel, detail, run_id, iter_):
        _, ids = ops_target(pid, rel["id"])
        try:
            nv = opsruns.reader(settings).node_values(run_id, rel.get("graph"), detail["node"], iter_)
        except opsruns.OpsUnavailable as e:
            raise HTTPException(status_code=503, detail=f"운영 기록을 읽을 수 없습니다 — {opsruns.REASON.get(e.kind, e.kind)}")
        if not nv or nv["run"]["deployment_id"] not in ids or nv["run"]["pack_revision"] != rel["pack"]["revision"]:
            raise HTTPException(status_code=404, detail="이 버전의 운영 Run이 아닙니다")
        run = nv.pop("run")
        if not nv["visited"]:
            return {**nv, "run_id": run["run_id"]}
        reads, writes = [r["name"] for r in detail["reads"]], [w["name"] for w in detail["writes"]]
        inp, out = nv["input"], nv["output"]
        nv["relations"] = node_detail.relations_of(out)   # D-543 ⑥ ①: which relation the advice came from
        nv["input"] = {k: inp[k] for k in reads if isinstance(inp, dict) and k in inp} if inp is not None else None
        nv["output"] = {k: out[k] for k in writes if isinstance(out, dict) and k in out} if out is not None else None
        nxt = [t for (s, t) in (e.split(">", 1) for e in run["passed"]) if s == detail["node"]]
        return {**nv, "run_id": run["run_id"], "next": nxt, "not_recorded": opsruns.not_recorded(detail["kind_name"], nv.get("meta"))}

    def node_detail_from(rel, copy, node_id, run, case, iter_, tolerate=None):
        rid = rel["id"]
        loaded = node_detail.load(copy, tolerate) if Path(copy).is_dir() else None
        if not loaded:
            raise HTTPException(status_code=422, detail="Agent Pack 사본의 pack.yaml을 읽을 수 없습니다 — 다시 등록하세요")
        gnode = next((n for n in (rel.get("graph") or {}).get("nodes", []) if n["name"] == node_id), None)
        fn_info = {k: gnode[k] for k in ("fn_loc", "source_sha256") if gnode and k in gnode}
        entry = rel["pack"].get("entrypoint") or rel["pack"]["entrypoints"][0]
        detail = node_detail.static_detail(*loaded, entry, node_id, fn_info, options=autohyp.pack_options(copy).get(node_id)) if entry in loaded[0]["graphs"] else None   # D-492 덧 28
        if detail is None:
            raise HTTPException(status_code=404, detail="이 release 그래프에 없는 node입니다")
        detail["run_value"] = None
        if run:
            r = store.run(run)
            if not r or r["rel"] != rid:
                raise HTTPException(status_code=422, detail="이 release의 Run이 아닙니다")
            if not 0 <= case < len(r["cases"]):
                raise HTTPException(status_code=422, detail="Run에 그 case가 없습니다")
            detail["run_value"] = node_detail.run_value(detail, r["cases"][case].get("visits"), iter_,
                                                        index_releases=(r.get("manifest") or {}).get("evidence_index_releases"))
        detail["graph"] = entry
        if not detail["structure"]["function_node"]:   # what the edit form chooses from (EV-018 CHG-86)
            detail["form"] = node_detail.form(copy, loaded[0], entry)
        return detail

    async def edit_body(request):
        """The body of a node edit (preview and edit): JSON up to 1 MB with 1~100 ops (EV-018 CHG-87)."""
        if int(request.headers.get("content-length") or 0) > PREVIEW_MAX_BYTES:   # refused before the body is read
            raise HTTPException(status_code=422, detail="수정 내용이 너무 큽니다(1 MB 이하)")
        raw = await request.body()
        if len(raw) > PREVIEW_MAX_BYTES:
            raise HTTPException(status_code=422, detail="수정 내용이 너무 큽니다(1 MB 이하)")
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            raise HTTPException(status_code=422, detail="본문이 JSON이 아닙니다")
        ops = body.get("ops") if isinstance(body, dict) else None
        if not isinstance(ops, list) or not 1 <= len(ops) <= PREVIEW_MAX_OPS:
            raise HTTPException(status_code=422, detail=f"수정 목록(ops)은 1~{PREVIEW_MAX_OPS}개의 목록이어야 합니다")
        return body, ops

    def pack_copy(rel):
        copy = Path(rel["pack"].get("copy", ""))
        if not copy.is_dir():
            raise HTTPException(status_code=422, detail="Agent Pack 사본이 없습니다 — 다시 등록하세요")
        return copy

    @app.post("/api/projects/{pid}/releases/{rid}/patch-preview")
    async def patch_preview(pid: str, rid: str, request: Request):
        """Check node edits on a temporary copy of the release copy (EV-018 CHG-87): pass/refuse with places, changed lines, IR
        diff and the new revision. Nothing is saved; the data folder, the release copy and the work folder are only read."""
        need_project(pid)
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        if rel["sourceKind"] != "agent_pack":
            raise HTTPException(status_code=422, detail="Agent Pack release만 미리보기를 할 수 있습니다")
        body, ops = await edit_body(request)
        copy = pack_copy(rel)
        res = await run_in_threadpool(editing.check, copy, [{"ops": ops}])   # a temporary folder, removed before it returns; EV-056: the edit check's rule
        old = lambda rel_path: (copy / rel_path).read_text(encoding="utf-8", errors="replace") if (copy / rel_path).is_file() else ""
        return {"ok": res.ok, "errors": res.errors, "warnings": res.warnings, "base_revision": res.base_revision,
                "release_revision": rel["pack"].get("revision"), "revision": res.revision,
                "changed_files": [{"path": p, "hunks": node_detail.hunks(old(p), res.files[p].decode("utf-8", "replace"))} for p in res.changed_files],
                "ir_diff": res.ir_diff}

    # ------------------------------------------------------------------ EV-022 E-S-02: edits, confirm, versions, layout
    # API contract v1.2 (.vibe-factory/evolutions/EV-022-edit-version-flow/execution/api-contract.md); manual p.8~10 · 20~22 · 43.
    def need_pack(pid, rid):
        need_project(pid)
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        if rel["sourceKind"] != "agent_pack":
            raise HTTPException(status_code=422, detail="Agent Pack release만 편집할 수 있습니다")   # S-17
        return rel

    def edit_view(e):
        if not e:
            return None
        return {k: e[k] for k in ("edit_id", "release", "base_revision", "revision", "check", "lines", "graph", "started_at")}

    def need_edit(pid):
        need_project(pid)
        e = store.open_edit(pid)
        if not e:
            raise HTTPException(status_code=404, detail="편집이 없습니다")
        return e

    def file_hunks(base_dir, res):
        old = lambda p: (Path(base_dir) / p).read_text(encoding="utf-8", errors="replace") if (Path(base_dir) / p).is_file() else ""
        return [{"path": p, "hunks": node_detail.hunks(old(p), res.files[p].decode("utf-8", "replace"), context=3)} for p in res.changed_files]

    def origin_release(rel):
        """The release a confirmed version started from, followed back to the one registered from a work folder (EV-022)."""
        return packs.origin_release(store, rel)

    def deploy_dir():
        return settings.deploy_status_dir or REPO_ROOT.parent / "llmopsagent-deploy" / "status" / "deployments"

    epoch = deploying.epoch_of

    def status_dirs(rel):
        """Where the two sessions write their status files for this version (instruction 019f): settings first (tests, e2e), else the
        Pack repository of the version's origin release and the neighbouring deploy repository. Read only."""
        work = Path((origin_release(rel).get("pack") or {}).get("work_folder") or ".")
        pack_dir = ci.status_dir if ci else settings.pack_status_dir or work.parent / "status" / "approved-changes"
        return pack_dir, deploy_dir()

    def approval_progress(d, rel):
        if d["evidence"].get("kg_gate"):   # D-588: the deploy side's gate — no status file, switched at the signal's time
            return kglink.progress(d["evidence"])
        pdir, ddir = status_dirs(rel)
        cid = d["evidence"].get("change_id")
        dev = d["evidence"].get("source") == "developer_change"   # EV-037: no request file, no runner request — the Pack tagged it itself
        if ci and cid and not dev:
            ci.refresh(cid)   # VPS: the runner's state becomes the Pack status file (throttled, never raises)
        pst = deploying.read_status(pdir, cid)
        prog = deploying.progress(pst, deploying.read_status(ddir, cid), request_note=ci.failure_note(cid) if ci and cid and pst is None and not dev else None)
        if dev:
            prog["steps"][0]["detail"] = f"개발자 변경(D-164) — 요청 파일은 Pack 쪽({cid})"
        # EV-039 CHG-199: an approval the runner took in rehearsal mode and whose run is over (passed or failed) will not go on —
        # its results count as absent (CHG-135), so it is not 'in progress' for the Project's rollback (D-140); its own lock stays
        rec = ci.record(cid) if ci and cid and not dev else None
        prog["rehearsal_done"] = bool(rec and rec.get("dry_run") is True and rec.get("done"))
        return prog

    def pack_change(rel):
        """EV-037 CHG-191: what the deploy window shows before the approval of a version without pack.from — the Pack status file of its
        revision (id · tag) or the reason it cannot be approved. Read only."""
        pdir, _ = status_dirs(rel)
        hits = deploying.find_status(pdir, rel["pack"].get("revision"))
        if not hits:
            return {"none": DEV_NONE}
        if len(hits) > 1:
            return {"none": DEV_MANY.format(ids=", ".join(h["id"] for h in hits))}
        h = hits[0]
        if h.get("stage") != "tagged":
            return {"none": DEV_UNTAGGED.format(id=h["id"], stage=h.get("stage"))}
        return {"id": h["id"], "tag": h.get("tag"), "commit": h.get("commit"), "path": str(Path(pdir) / f"{h['id']}.json")}

    def dev_change_approval(pid, rid, rel, view, dep, who, why, base, index=None):
        """EV-037 CHG-191: decision(deploy_approved) only — change id = the Pack status file's id, no llmops request file, no runner
        request. Refused (409, nothing written): no file · more than one · not tagged · the id already approved · a llmops request file of
        that id exists (a confirmed version's change). Same-id requests pass one at a time."""
        pc = pack_change(rel)
        if pc.get("none"):
            raise HTTPException(status_code=409, detail=pc["none"])
        cid = pc["id"]
        with DEV_CHANGE_LOCK:
            drs = versions.release_drs(rel)   # D-484 덧 4: the same id with another execution config (drs) is approved again
            if deploying.change_approved(cid, drs, store.deploy_approvals(), lambda r: versions.release_drs(store.release(r)) if r else None):
                raise HTTPException(status_code=409, detail=f"이 Pack 변경({cid})은 이미 배포 승인되었습니다")
            if (settings.data_dir / "approved-changes" / f"{cid}.json").exists():
                raise HTTPException(status_code=409, detail=f"이 Pack 변경({cid})은 llmops 확정 길의 요청 파일이 있습니다 — 그 버전에서 배포 승인합니다")
            ap = view.get("approval") or {}
            did = store.add_deploy_approval(deployment_id=dep, decided_by=who, reason=why,
                                            evidence={"release": rid, "version": rel["name"], "revision": rel["pack"]["revision"], "base_revision": None,
                                                      "change_id": cid, "request": None, "approval": ap, "target_base": base, "source": "developer_change",
                                                      "drs": drs,
                                                      "model": approved_model(rel),
                                                      "pack_status": {"path": pc["path"], "commit": pc.get("commit"), "tag": pc.get("tag")},
                                                      **({"index": index} if index else {})})
        return {"decision_id": did, "change_id": cid, "request": None, "deployment_id": dep, "source": "developer_change",
                "pack_ci": {"requested": False, "reason": "개발자 변경 — Pack 쪽에서 이미 태그"} if ci else None}

    def approved_model(rel):
        """EV-044 CHG-220 (Q-A): the model release the approved version was registered and evaluated with — written on its ledger row
        (the data bundle stays the target's, 019h)."""
        mrs, binds = deploying.model_line(((rel.get("pack") or {}).get("bindings") or {}).get("models"))
        return {"model_release_id": mrs, "model_bindings": binds}

    def index_pick(rel, run_id, base, memo=None):
        """EV-051 CHG-W2-22 · 23 · 24: (the changed stores' values from the verdict Run, evidence.index) — (None, None) when this
        version's graph has no retrieve@2, there is no base (019h as before) or nothing changed against the Pack the target runs. 409
        before anything is written: a base without the target's revision, the target's Pack not here, a Pack that does not read, or
        the verdict Run (this version's) without the versions. memo["run"] · ["all_releases"]: what /api/state already read."""
        if not base:
            return None, None
        have = (memo or {}).get("run") or {}
        run = (have[run_id] if run_id in have else store.run(run_id)) if run_id else None
        graph = (((run or {}).get("manifest") or {}).get("execution_config") or {}).get("entrypoint") or rel["pack"].get("entrypoint")
        try:
            mine = deploying.pack_view(rel["pack"]["copy"], graph)
        except Exception as e:  # noqa: BLE001 - refused, never a guess
            raise HTTPException(status_code=409, detail=f"이 버전의 Pack을 읽을 수 없어 벡터 DB 바꿈을 확인하지 못함({type(e).__name__})") from None
        if not mine[2]:   # no retrieve@2 here: nothing to compare, the target's Pack is never read (review-2 N-W24a-11)
            return None, None
        if not base.get("pack_revision"):
            raise HTTPException(status_code=409, detail="배포 대상 줄의 revision을 몰라 벡터 DB 바꿈을 확인하지 못함")
        rels = (memo or {}).get("all_releases")
        if rels is None:
            rels = [r for p in store.projects() for r in store.releases(p["id"])]
        target = deploying.pack_copy_of(rels, rel["pack"]["id"], base["pack_revision"])
        if target is None:
            raise HTTPException(status_code=409, detail="운영 버전의 Pack을 찾을 수 없어 벡터 DB 바꿈을 확인하지 못함")
        try:
            changed = deploying.index_changed_stores(deploying.pack_view(target, graph), mine)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=f"운영 버전의 Pack을 읽을 수 없어 벡터 DB 바꿈을 확인하지 못함({type(e).__name__})") from None
        if not changed:
            return None, None
        picked, why = deploying.index_of_run(run, changed, rel["id"], rel["pack"]["revision"])
        if why:
            raise HTTPException(status_code=409, detail=f"판정 Run의 벡터 DB version을 확인하지 못함({why})")
        return picked, deploying.index_overlay(base, picked)

    def index_evidence(rel, run_id, base):
        return index_pick(rel, run_id, base)[1]

    def index_preview(rel, run_id, base, dflt, memo=None):
        """CHG-W2-25: what the deploy window says before the approval, for the default deployment id — {index_changes, index_drs,
        index_for} or {index_note, index_for} or {}."""
        try:
            picked, idx = index_pick(rel, run_id, base, memo)
        except HTTPException as e:
            return {"index_note": str(e.detail), "index_for": dflt}
        if not idx:
            return {}
        return {"index_changes": [{"store": s, "release_id": v["release_id"], "embedding_mode": v["embedding_mode"]} for s, v in picked.items()],
                "index_drs": idx["data_release_id"], "index_for": dflt}

    app.state.index_evidence = index_evidence   # the two deploy approval paths call it once each (wired on EV-054's running_base)

    def ledger_row(d, rel, prog, memo=None):
        """S-20: once the Pack session reported `tagged`, LLMOps adds its own deployment row for this deploy approval (add only;
        the same key again does nothing). The row describes the approved version: its revision and run bindings."""
        pk, st, base = rel["pack"], prog.get("pack") or {}, d["evidence"].get("target_base")
        if st.get("revision") and st["revision"] != pk.get("revision"):
            return "상태 파일의 revision이 승인한 버전과 다릅니다 — 배포 기록 줄을 쓰지 않았습니다"
        if not base:
            return "배포 대상의 실행 설정 값이 승인 기록에 없어 배포 기록 줄을 쓰지 않았습니다"
        idx = d["evidence"].get("index")   # EV-051 CHG-W2-24: the stores this version's vector DB changed carry its versions
        try:   # 019h: the data bundle and bindings the deployment runs with (the target's), not the evaluated ones
            wrote = store.register_deployment(decision_id=d["decision_id"], deployment_id=d["subject_id"], pack_id=pk["id"], pack_revision=pk["revision"],
                                      data_release_id=(idx or base)["data_release_id"],
                                      release_bindings=deploying.index_bindings(base, idx["stores"]) if idx else base["release_bindings"],
                                      runtime_version=base["runtime_version"], config_sha256=base["config_sha256"],
                                      pack_commit=st.get("commit"), pack_tag=st.get("tag"),
                                      **({"model_release_id": d["evidence"]["model"].get("model_release_id"),
                                          "model_bindings": d["evidence"]["model"].get("model_bindings")}
                                         if isinstance(d["evidence"].get("model"), dict) else {}))   # an approval before EV-044: no model
        except (ValueError, *DB_ERRORS) as e:
            return f"배포 기록 줄을 쓰지 못했습니다 — {e}"
        if wrote and memo is not None:   # state-n1: a new ledger row — the answer's deployments are read again by the next reader
            memo.pop("deployments", None)
        return None

    def rollouts(pid, memo=None):
        """Deploy approvals of the Project's versions (CHG-113, S-6): the latest one per release, with its four steps read from the
        status files. Reading `tagged` adds LLMOps' deployment row once (S-20). memo (state-n1): /api/state's releases · approvals,
        read once for every Project; a row written here drops its deployments so the next reader reads them again."""
        out = {}
        m = memo if memo is not None else {}
        rels = {r["id"]: r for r in (m["by_pid"][pid] if pid in m.get("by_pid", {}) else store.releases(pid)) if r.get("pack")}
        if memo is not None and "approvals" not in memo:
            memo["approvals"] = store.deploy_approvals()
        for d in (memo["approvals"] if memo is not None else store.deploy_approvals()):
            rel = rels.get(d["evidence"].get("release"))
            if not rel:
                continue
            prog = approval_progress(d, rel)
            note = ledger_row(d, rel, prog, memo) if prog["tagged"] else None
            out[rel["id"]] = {**deploying.rollout_row(prog, d), "ledger_note": note}
        return out

    def rollback_views(pid, releases=None, decisions=None):
        """EV-SUI-01 CHG-S02 · S03: the Project's rollbacks (D-140), oldest first, each with its three steps read from the deploy
        side's status file named by the rollback id. Read only. releases · decisions: already read (EV-052 PRES-246)."""
        rels = {r["id"]: r for r in (store.releases(pid) if releases is None else releases) if r.get("pack")}
        out = []
        for d in (store.rollbacks() if decisions is None else decisions):
            ev = d["evidence"]
            target, frm = rels.get(ev.get("release")), rels.get(ev.get("from_release"))
            if not target or not frm:
                continue
            prog = deploying.rollback_progress(deploying.read_status(deploy_dir(), ev.get("rollback_id")))
            out.append({**prog, "rollback_id": ev.get("rollback_id"), "decision_id": d["decision_id"], "target": target["id"], "target_name": target["name"],
                        "from": frm["id"], "from_name": frm["name"], "deployment_id": d["subject_id"], "decided_by": d["decided_by"], "reason_text": d["reason"],
                        "command": ev.get("command"), "revision": ev.get("revision"), "pack_id": target["pack"].get("id"), "decided_at": d["decided_at"],
                        "at": time.strftime("%m-%d %H:%M", time.localtime(d["decided_at"]))})
        return out

    def switch_time(row, progress=None, decision=None, release=None):
        """When a tagged deployment row started running: its deploy approval's `switched` time, or the row's own time for a row
        without a status file (an earlier bootstrap row). None when the status file's time cannot be read (CHG-S01). A switch
        cannot come before its own ledger row (written at `tagged`), so a status-file time earlier than the row reads as the
        row's time (a status file copied or written with another clock keeps the row order)."""
        d = (decision or store.decision)(row["decision_id"])
        if d and d["decision"] == "deploy_approved":
            rel = (release or store.release)(d["evidence"].get("release"))
            dep = ((progress or approval_progress)(d, rel).get("deploy") or {}) if rel else {}
            t = epoch(dep.get("updated_at"))
            return None if t is None else max(t, row["created_at"])
        return row["created_at"]

    RUNNING_NOT_YET = "배포단이 아직 이 줄로 돌지 않았습니다 — 배포단에서 보통 Run 한 번 뒤 다시 승인합니다({})"   # EV-054 CHG-233 ㉠
    RUNNING_UNDECIDED = "배포 대상 {}에서 지금 도는 배포를 정할 수 없습니다({})"                                 # ㉡
    RUNNING_NO_TRACE = "운영 기록(trace)을 읽을 수 없어 배포단이 지금 도는 줄을 정할 수 없습니다 — {}"            # ㉢

    def running_base(pack_id, deployment_id, memo=None):
        """EV-054 CHG-232 · 233 (D-355 (3) · D-359, deploy 154): (target_base, note) for a new deployment of this Pack on this deploy
        target — the row the deploy side runs now. ① the target's tagged rows ② the target's current service = the deployment id of
        its last switch (a bootstrap row, a switched deploy approval, a switched rollback of this Pack in any Project; an R12 row is no
        switch) ③ the signal = that deployment id's last completed Run in trace (opsruns.last_succeeded) ④ the row of that deployment
        id with the Run's four values (a row without a model matches any). No base (None, note ㉠ ~ ㉢) when it cannot be told; (None,
        None) when the target has no row (the caller's earlier text ㉣). memo: one computation's decisions, releases, progress,
        rollbacks and signals (EV-052)."""
        memo = {} if memo is None else memo
        decs, rls, prog, sigs = (memo.setdefault(k, {}) for k in ("decision", "release", "progress", "signal"))
        decision = lambda i: decs[i] if i in decs else decs.setdefault(i, store.decision(i))
        release = lambda i: rls[i] if i in rls else rls.setdefault(i, store.release(i))

        def progress(d, rel):
            if d["decision_id"] not in prog:
                prog[d["decision_id"]] = approval_progress(d, rel)
            return prog[d["decision_id"]]
        rows = store.deployments()
        target = deploying.target_in(deployment_id, rows)   # EV-067 (D-425): a version-free id with its tagged row is its own target
        mine = [r for r in rows if r.get("pack_id") == pack_id and r.get("pack_tag") and deploying.same_target(r.get("deployment_id"), target)]
        if not mine:
            return None, None
        events, bad = [], None   # (time or None, deployment id, recorded)
        for r in mine:
            d = decision(r["decision_id"])
            kind = d and d["decision"]
            if kind == "data_release_auto":
                continue                                   # an R12 data change is not a switch (EV-044 CHG-225)
            if kind == "deploy_approved":
                rel = release(d["evidence"].get("release"))
                if not rel:
                    bad = r["deployment_id"]               # E3-A1-N2 (b): a switch that cannot be read closes, not skipped
                    continue
                pr = progress(d, rel)
                if pr["failed"] or pr["stage"] != "switched":
                    continue
                events.append((switch_time(r, progress, decision, release), r["deployment_id"], r["created_at"]))
            else:
                events.append((r["created_at"], r["deployment_id"], r["created_at"]))   # a bootstrap row counts by its tag
        if "rollbacks" not in memo:
            memo["rollbacks"] = store.rollbacks()
        for b in memo["rollbacks"]:
            ev = b["evidence"]
            dep = ev.get("target_deployment_id") or b["subject_id"]
            if (ev.get("pack_id") or "") != pack_id or not deploying.same_target(dep, target):
                continue
            st = deploying.rollback_progress(deploying.read_status(deploy_dir(), ev.get("rollback_id")))
            if st["done"]:
                at = epoch(st.get("updated_at"))
                events.append((None if at is None else max(at, b["decided_at"]), dep, b["decided_at"]))
        if bad:
            return None, RUNNING_UNDECIDED.format(target, f"release를 읽을 수 없는 배포 승인 {bad}")
        known = sorted((e for e in events if e[0] is not None), key=lambda e: (e[0], e[2]))
        if not known:
            return None, RUNNING_UNDECIDED.format(target, "전환 사건 없음" if not events else versions.UNREADABLE)
        last = known[-1]
        if any(e[0] is None and e[2] >= last[2] for e in events):
            return None, RUNNING_UNDECIDED.format(target, versions.UNREADABLE)
        if len({e[1] for e in known if e[0] == last[0]}) > 1:   # E4-F1-N3 (a): every switch at the last time, not only the one before
            return None, RUNNING_UNDECIDED.format(target, versions.SAME_TIME)
        current = last[1]
        deps = sorted({r["deployment_id"] for r in mine})
        key = tuple(deps)
        if key not in sigs:
            try:
                sigs[key] = opsruns.last_succeeded(settings, deps)
            except opsruns.OpsUnavailable as e:
                sigs[key] = e
        if isinstance(sigs[key], opsruns.OpsUnavailable):
            return None, RUNNING_NO_TRACE.format(opsruns.REASON.get(sigs[key].kind, sigs[key].kind))
        runs, confirm_all = sigs[key]
        rows = [r for r in mine if r["deployment_id"] == current]
        run = runs.get(current)
        if run is None and confirm_all and rows:   # CHG-234 harness: the current service's last row ran just now (recorded at +∞)
            run = {"run_id": None, "revision": rows[-1]["pack_revision"], "drs": rows[-1]["data_release_id"],
                   "mrs": rows[-1].get("model_release_id"), "recorded_at": float("inf")}
        if run is None or run["recorded_at"] is None:
            return None, RUNNING_NOT_YET.format(current)
        later = sorted(k for k, x in runs.items() if k != current and x["recorded_at"] is not None and x["recorded_at"] > run["recorded_at"])
        if later:   # Q-5 (D-359 덧): another deployment of the target ran later — a switch made outside LLMOps
            return None, RUNNING_UNDECIDED.format(target, f"다른 배포 {later[0]}가 더 늦게 돎")
        if any(r["created_at"] > run["recorded_at"] for r in rows):
            return None, RUNNING_NOT_YET.format(current)
        match = [r for r in rows if r["pack_revision"] == run["revision"] and r["data_release_id"] == run["drs"]
                 and (not r.get("model_release_id") or r.get("model_release_id") == run["mrs"])]
        if not match:
            return None, RUNNING_NOT_YET.format(current)
        r = match[-1]
        binds = r["release_bindings"]
        return ({"deployment_id": r["deployment_id"], "data_release_id": r["data_release_id"],
                 "release_bindings": json.loads(binds) if isinstance(binds, str) else binds, "config_sha256": r["config_sha256"],
                 "runtime_version": r["runtime_version"], "run_id": run["run_id"],
                 "pack_revision": r["pack_revision"]}, None)   # D-360 덧: the chosen row's revision (llmops-2 W2-4a finds the running Pack by it)

    app.state.running_base = running_base   # EV-054: for the tests of the rule itself (the routes call it directly)

    def operational_of(pid, rels, rows, memo=None):
        """EV-052 CHG-227: (운영 release id, earlier ids, info, rollback views) of a Project — the one rule the version badges, the
        automatic diagnosis (auto_targets) and D-168 (project_elsewhere) share: status-file stages and switch times (CHG-S01) and
        the Project's switched rollbacks. memo (one dict for one computation) keeps the decisions, releases, approval_progress
        (once per decision — deploy_stage and switch_time share it) and store.rollbacks(); None = a memo of this call only."""
        memo = {} if memo is None else memo
        decs, rls, prog = memo.setdefault("decision", {}), memo.setdefault("release", {}), memo.setdefault("progress", {})

        def decision(i):
            if i not in decs:
                decs[i] = store.decision(i)
            return decs[i]

        def release(i):
            if i not in rls:
                rls[i] = store.release(i)
            return rls[i]

        def progress(d, rel):
            if d["decision_id"] not in prog:
                prog[d["decision_id"]] = approval_progress(d, rel)
            return prog[d["decision_id"]]
        if "rollbacks" not in memo:
            memo["rollbacks"] = store.rollbacks()
        rbs = rollback_views(pid, rels, memo["rollbacks"])
        switched = [{"revision": b["revision"], "pack_id": b["pack_id"], "recorded": b["decided_at"],
                     "at": None if (t := epoch(b.get("updated_at"))) is None else max(t, b["decided_at"])}
                    for b in rbs if b["done"]]   # a rollback cannot switch before its own decision (as for the ledger rows)
        # D-457 ③-8: a switched deploy approval of this Project with no ledger row of its own because a tagged row of the same key
        # (deployment id · revision) was already there — the same revision deployed again (V7 → V4 → V8) — counts as its own version
        if "approvals" not in memo:
            memo["approvals"] = store.deploy_approvals()
        mine, have = {r["id"] for r in rels if r.get("pack")}, {r["decision_id"] for r in rows}
        redeploys = []
        for d in memo["approvals"]:
            rid_ = d["evidence"].get("release")
            if d["decision_id"] in have or rid_ not in mine:
                continue
            rel_ = release(rid_)
            if not rel_ or not any(r.get("pack_tag") and r["deployment_id"] == d["subject_id"] and r["pack_revision"] == rel_["pack"]["revision"] for r in rows):
                continue
            prog_ = progress(d, rel_)
            if prog_["failed"] or prog_["stage"] != "switched":
                continue
            t_ = epoch((prog_.get("deploy") or {}).get("updated_at"))
            redeploys.append({"release": rid_, "at": None if t_ is None else max(t_, d["decided_at"]), "recorded": d["decided_at"]})
        info = {}
        op, earlier = versions.operational(rels, rows, stage_of=lambda r: deploy_stage(r, progress, decision, release),
                                           when_of=lambda r: switch_time(r, progress, decision, release), rollbacks=switched, info=info,
                                           redeploys=redeploys)
        if info.get("current_kind") == "deployment" and any(rd["release"] == op and rd["at"] == info.get("current_at") for rd in redeploys):
            info["redeploy_at"] = info["current_at"]   # D-457 ③-8: '운영 …부터' = this approval's own switch, not the older row's
        return op, earlier, info, rbs

    def version_views(p, memo=None, with_base=False):
        """{release id: version view} for the Project's Agent Pack releases (CHG-107), with the deploy approval's steps (CHG-113)
        and the deployment a 운영 / 이전 version runs as (p.5 · 29); EV-SUI-01: the rollback of the 운영 version (D-140)."""
        rels = p["releases"]
        ro = rollouts(p["id"], memo)   # first: reading `tagged` may add a deployment row
        rows = memo["deployments"] if memo and "deployments" in memo else store.deployments()   # state-n1: the answer's one read
        if memo is not None and "deployments" not in memo and "by_pid" in memo:   # read again after a new ledger row: kept for the next Project
            memo["deployments"] = rows
        op, earlier, info, rbs = operational_of(p["id"], rels, rows, memo)
        live = any(x and not x.get("failed") and x.get("stage") != "switched" and not x.get("rehearsal_done") for x in ro.values()) or \
            any(not b["done"] and not b["failed"] for b in rbs)
        have = (memo or {}).get("runs") or {}
        runs = have[p["id"]] if p["id"] in have else store.runs(p["id"])   # the stored Runs (with their code hash), not the screen view
        decs = {} if memo is None else memo.setdefault("decision", {})

        def decision(i):
            if i not in decs:
                decs[i] = store.decision(i)
            return decs[i]
        auto_ids = {x["decision_id"] for x in rows if (decision(x["decision_id"]) or {}).get("decision") == "data_release_auto"}   # F-N1: always (an R12 row
        # written by the command right after 0010, before this server restarts, must not move '운영 …부터' either)
        op_rows = [x for x in rows if op and x.get("pack_tag") and any(o["id"] == op for o in versions.row_versions(rels, x))]   # EV-071 (D-443 ③)
        out = {}
        ffs = store.fixed_flows(r["id"] for r in rels if r.get("pack")) if store.auto_ready else {}   # D-485: every band's fixed flow, one read
        for r in rels:
            if not r.get("pack"):
                continue
            v = versions.state(r, runs=runs, op=op, earlier=earlier, rollout=ro.get(r["id"]), releases=rels)
            v["rollout"] = ro.get(r["id"])
            if store.auto_ready:
                v["fixed_flow"] = fixedflow.shown(ffs.get(r["id"]), r["pack"].get("revision"))
            if v["state"] == "승인됨" and not v["rollout"] and r.get("name"):
                dflt = deploying.window_deployment_id(op_rows, r["pack"]["id"], r["name"])   # EV-071: the 운영 deployment's id
                if with_base:   # EV-054 CHG-232 · 233: the screen's deploy window (/api/state) only — no other caller reads it (PRES-254)
                    b, note = running_base(r["pack"]["id"], dflt, memo)
                else:
                    b, note = deploying.target_base(rows, r["pack"]["id"], dflt), None
                v["deploy_target"] = {"deployment_id": dflt, "data_release_id": b and b["data_release_id"], "from": b and b["deployment_id"]}
                if note:
                    v["deploy_target"]["note"] = note
                if with_base and b:   # EV-051 CHG-W2-25: a vector DB this version changed, before the approval
                    v["deploy_target"].update(index_preview(r, (v.get("approval") or {}).get("run"), b, dflt, memo))
                if not (r["pack"].get("from")):   # EV-037: a developer-change version shows its Pack change before the approval
                    v["deploy_target"]["pack_change"] = pack_change(r)
            mine = [x for x in rows if x.get("pack_tag") and any(o["id"] == r["id"] for o in versions.row_versions(rels, x))
                    and x["decision_id"] not in auto_ids]   # EV-044: this version's rows (CHG-221), not the R12 data changes (CHG-225)
            v["data_changes"] = [{"at": time.strftime("%m-%d %H:%M", time.localtime(x["created_at"])), "reports": len(ev.get("reports") or []),
                                  "store": ev.get("store"), "previous": ev.get("previous"), "version": ev.get("version"), "deployment_id": x["deployment_id"]}
                                 for x in rows if x["decision_id"] in auto_ids and any(o["id"] == r["id"] for o in versions.row_versions(rels, x))
                                 for ev in [(decision(x["decision_id"]) or {}).get("evidence") or {}]]   # EV-044 CHG-223 (Q-D): R12 lines
            v["deployment"] = {"deployment_id": mine[-1]["deployment_id"], "tag": mine[-1]["pack_tag"],
                               "at": running_since(mine[-1], r)} if mine and r["id"] in ({op} | earlier) else None
            if r["id"] == op:
                if info.get("current_kind") == "rollback" and v["deployment"]:   # '운영 …부터' = the rollback's own switched time
                    made = next((b for b in reversed(rbs) if b["done"] and b["target"] == r["id"]), None)
                    t = epoch(made.get("updated_at")) if made else None
                    if t is not None:
                        v["deployment"]["at"] = time.strftime("%m-%d %H:%M", time.localtime(t))
                if info.get("redeploy_at") and v["deployment"]:
                    v["deployment"]["at"] = time.strftime("%m-%d %H:%M", time.localtime(info["redeploy_at"]))
                if info.get("note"):
                    v["op_note"] = info["note"]
                other = elsewhere(p["id"], r, memo)
                if other:   # D-168 (EV-034 CHG-170): rollback belongs to the deploy-approved Project; the badge stays
                    v["rollback_elsewhere"] = other
                mine_rb = [b for b in rbs if b["from"] == r["id"] and not b["done"]]
                if mine_rb:
                    v["rollback"] = mine_rb[-1]                      # in progress or failed (a failed one can be done again with a new id)
                prev = versions.previous_operational(info, op)
                prev_rel = next((x for x in rels if x["id"] == prev), None)
                if prev_rel and not versions.rollback_ok(prev_rel.get("name"), r.get("name")):
                    prev_rel = None                                  # back to an earlier version only (D-140: V1 → V0; '다시 V1로' is not this change; EV-068: by number only between V<n> names)
                came = next((b for b in reversed(rbs) if b["done"] and b["target"] == r["id"]), None) if info.get("current_kind") == "rollback" else None
                if prev_rel and came and came["from"] == prev_rel["id"]:
                    prev_rel = None                                  # D-428 덧 3 (가): never back to the version this one was just rolled back from, whatever the names
                prev_rows = [x for x in rows if prev_rel and x["pack_revision"] == prev_rel["pack"].get("revision") and x["pack_id"] == prev_rel["pack"].get("id") and x.get("pack_tag")]
                if other:
                    prev_rel = None                                  # no rollback here (D-168): neither busy nor a target
                if prev_rel and prev_rows and live:
                    v["rollback_busy"] = True                       # a deploy approval or a rollback is in progress somewhere in the Project
                elif prev_rel and prev_rows:
                    v["rollback_target"] = {"release": prev_rel["id"], "name": prev_rel["name"], "revision": prev_rel["pack"].get("revision"),
                                            "deployment_id": prev_rows[-1]["deployment_id"], "tag": prev_rows[-1].get("pack_tag"),
                                            "commit": prev_rows[-1].get("pack_commit")}   # D-633: the request file names what to switch to
            done_in = [b for b in rbs if b["target"] == r["id"] and b["done"]]
            if done_in:
                v["rolled_back"] = done_in[-1]                       # the record line of the rollback that made this version run (p.29 배포 기록)
            auto = ((r.get("pack") or {}).get("from") or {}).get("auto")
            if auto and store.auto_ready:   # EV-035 CHG-184: the mark only — the state stays computed as before (PRES-182)
                w = next((x for x in store.wheels(p["id"]) if x["seq"] == auto.get("wheel")), None)
                rec = [c["recommendation"] for c in (store.candidates(w["wheel_id"]) if w else []) if c.get("release_id") == r["id"] and c.get("recommendation")]
                if rec:
                    v["auto_rec"] = rec[-1]["decision"]
                    uid_of = lambda x: x["uid"]
                    judged = any(x.get("verdicts") for x in runs if x["rel"] == r["id"] and x.get("code_hash") == r.get("code_hash"))   # a person's verdict at this revision
                    pairs = (v.get("judge") or {}).get("pairs") or {}
                    target = next((x for x in reversed(runs) if x["uid"] in pairs), None)   # the latest same-condition Run (stored Runs are in start order)
                    rec_run = next((x for x in runs if x["uid"] == (rec[-1].get("new") or {}).get("run")), None)
                    same_rev = bool(rec_run) and rec_run.get("code_hash") == r.get("code_hash")   # the recommendation was made on this revision (ML5-B2)
                    if rec[-1]["decision"] in ("adopt", "hold") and v.get("state") == "평가 중" and not judged and target and same_rev:   # CHG-185 (PC-3, Q-3 A); a hold goes to the person as an adopt does (D-463 덧 31 · ORCH 240)
                        hs = {h["hypothesis_id"]: h for h in store.hypotheses(w["wheel_id"])}
                        row = next((c for c in store.candidates(w["wheel_id"]) if c.get("release_id") == r["id"]), {})
                        v["badge"] = "승인 대기"
                        v["await"] = {"wheel": f"A-{w['seq']}", "kind": w.get("kind"), "run": uid_of(target), "label": target.get("label"), "compare": pairs[uid_of(target)],
                                      "hypotheses": [(lambda c: f"H-{i} · 대상 {c['target']['word']} {c['target']['id']} · 바꿈 {c['change']} · 근거 {c['evidence']}")(hyp_cells(hs[i]))
                                                     for i in row.get("hypothesis_ids") or [] if i in hs],   # D-549 덧 3: 대상 · 바꿈 · 근거
                                      "reasons": ((rec_shown(w["wheel_id"], rec[-1]) or {}).get("reasons") or []) + ([row["table_result"]["covering"]] if (row.get("table_result") or {}).get("covering") else []),   # D-549 덧 3 A-2
                                      "hidden_none": not rec[-1].get("hidden"),
                                      "base": (rec[-1].get("base") or {}).get("label"), "new": (rec[-1].get("new") or {}).get("label"),
                                      "sealed": autosealed.line(rec[-1]["sealed"]) if rec[-1].get("sealed") else None,   # D-477 덧 13: a person called it
                                      "scoring": autocand.scoring_word(rec[-1])}   # EV-089: the count the recommendation was made with
                        dflt = deploying.window_deployment_id(op_rows, r["pack"]["id"], r["name"])   # EV-071: the window's default (승인 대기 too)
                        v["await"]["deployment_id"] = dflt
                        if with_base:   # EV-051 CHG-W2-25: the same line for a wheel version waiting for its one approval (N-G4-5)
                            ab, _ = running_base(r["pack"]["id"], dflt, memo)
                            if ab:
                                v["await"].update(index_preview(r, uid_of(target), ab, dflt, memo))
            out[r["id"]] = v
        for rid, v in out.items():   # D-687 12-e 덧: this version's change went out on another one — the band has no approval button
            if (moved := versions.moved_out(rels, out, rid)):
                v["moved_to"] = moved["name"]
        return out

    def running_since(row, rel):
        """K4 (S-16, manual p.29 '운영 …부터'): for a deployment made by a deploy approval, the time the deploy session switched
        (its status file's updated_at); the ledger row's own time otherwise (an earlier bootstrap row)."""
        d = store.decision(row["decision_id"])
        if d and d["decision"] == "deploy_approved":
            dep = approval_progress(d, rel).get("deploy") or {}
            if dep.get("stage") == "switched" and dep.get("updated_at"):
                try:
                    from datetime import datetime
                    return datetime.fromisoformat(dep["updated_at"]).strftime("%m-%d %H:%M")
                except ValueError:
                    pass
        return time.strftime("%m-%d %H:%M", time.localtime(row["created_at"]))

    def deploy_stage(row, progress=None, decision=None, release=None):
        """The status-file stage of a deployment row made by a deploy approval (S-16 revised: 운영 from `switched`); None for an
        earlier bootstrap row, which counts by its tag."""
        d = (decision or store.decision)(row["decision_id"])
        if d and d["decision"] == "data_release_auto":
            return versions.AUTO_STAGE   # EV-044 CHG-225: not a switch (versions.operational leaves it out)
        if not d or d["decision"] != "deploy_approved":
            return None
        rel = (release or store.release)(d["evidence"].get("release"))
        if not rel:
            return "tagged"
        prog = (progress or approval_progress)(d, rel)
        return "failed" if prog["failed"] else prog["stage"]

    @app.post("/api/projects/{pid}/releases/{rid}/rollback", status_code=201)
    def rollback(pid: str, rid: str, body: dict = Body(default_factory=dict)):
        """운영 되돌리기 (D-140, EV-SUI-01 CHG-S02 · S03): from the 운영 version back to the version that ran before it, which is
        already registered. One decision(deploy_rolled_back) with a new rollback id and (D-633) its request file llmops/rollback-request@1
        (<data>/approved-changes/<id>.json, whole or none · never twice · deploy 499) for the deploy side's watcher; no ledger row. On a VPS the operator
        may still run the shown `deploy-check rollback` line; the steps follow the deploy side's status file of that id."""
        p = store.project(pid)
        rel = need_pack(pid, rid)
        view = version_views(p).get(rid) or {}
        if view.get("state") != "운영":
            raise HTTPException(status_code=409, detail="운영 버전에서만 되돌립니다")
        other = elsewhere(pid, rel)
        if other:   # D-168: right after the 운영 check, so the reason is this one (no decision, no command)
            raise HTTPException(status_code=409, detail=f"되돌리기는 배포 승인을 받은 Project {other}에서만 합니다")
        if view.get("rollback") and not view["rollback"].get("failed"):
            raise HTTPException(status_code=409, detail="배포 승인 또는 되돌리기가 진행 중입니다")
        tgt = view.get("rollback_target")
        if not tgt:
            raise HTTPException(status_code=409, detail="배포 승인 또는 되돌리기가 진행 중입니다" if view.get("rollback_busy") else "되돌릴 이전 버전이 없습니다")
        who, why = (body.get("decided_by") or "").strip(), (body.get("reason") or "").strip()
        problems = [x for x in (deploying.alias_error(who), None if why else "이유를 적어야 되돌리기가 저장됩니다") if x]
        if problems:
            raise HTTPException(status_code=422, detail={"kind": "form", "reasons": problems})
        n = 1 + sum(1 for d in store.rollbacks() if (d["evidence"].get("pack_id") or "") == rel["pack"]["id"])
        rb_id = f"RB-{rel['pack']['id']}-{re.sub(r'[^A-Za-z0-9._-]+', '-', tgt['name'])}-{n}"   # EV-068: the deploy side's status file name — safe letters only
        from_dep = (view.get("deployment") or {}).get("deployment_id")
        command = deploying.rollback_command(rb_id, tgt["name"], tgt["deployment_id"], from_dep)
        try:
            doc = deploying.rollback_request_doc(rollback_id=rb_id, pack_id=rel["pack"]["id"], decided_by=who, reason=why, decided_at=time.time(),
                                                 target={**tgt, "version": tgt["name"]},
                                                 from_={"version": rel["name"], "release": rid, "revision": rel["pack"].get("revision"), "deployment_id": from_dep})
        except ValueError as e:
            raise HTTPException(status_code=409, detail=f"{e.args[0]} — 되돌리기를 저장하지 않았습니다")
        folder = settings.data_dir / "approved-changes"   # deploy 499: the folder the watcher reads (its CHG way keeps to format · CHG-)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{rb_id}.json"
        tmp = folder / f".{rb_id}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        try:
            os.link(tmp, path)   # whole file or none, and only when it is not there yet (as the deploy request file)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="같은 되돌리기의 요청 파일이 이미 있습니다")
        finally:
            tmp.unlink(missing_ok=True)
        try:
            did = store.add_rollback(deployment_id=tgt["deployment_id"], decided_by=who, reason=why,
                                     evidence={"release": tgt["release"], "version": tgt["name"], "revision": tgt["revision"], "from_release": rid,
                                               "target_deployment_id": tgt["deployment_id"],
                                               "from_version": rel["name"], "from_deployment_id": from_dep, "rollback_id": rb_id, "pack_id": rel["pack"]["id"],
                                               "command": command, "request": str(path)})
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"decision_id": did, "rollback_id": rb_id, "deployment_id": tgt["deployment_id"], "command": command, "request": str(path)}

    @app.post("/api/projects/{pid}/sealed-check")
    def sealed_check(pid: str, body: dict = Body(default_factory=dict)):
        """D-477 덧 13 (packrt-1 070 덧 1): the sealed test on the one candidate a person picked — the wheel calls it only after an
        adopt, and a hold (thin evidence) never reached it. The wheel's own run (autosealed.check: in memory · no Run row · no trace · no
        judge); the counts stay on the candidate's recommendation ('sealed', the ⑥ row). A version once on a set, a set at most its limit:
        409; no setting: '설정 없음', nothing kept."""
        need_project(pid)
        who, rid = str(body.get("by") or "").strip(), str(body.get("release_id") or "")
        why = deploying.alias_error(who)
        if why:   # packrt-1 073 제안 2: 'owner-01(사용자 …)' — the form, brackets and spaces named
            raise HTTPException(status_code=422, detail={"kind": "by", "message": f"{why} · 괄호 · 공백 안 됨"})
        rel = store.release(rid)
        if not rel or rel["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        key_stop(rel, main.settings.mode)   # D-492 덧 24: the sealed Runs would start without the key file (10-31: 108 cases failed)
        auto = ((rel.get("pack") or {}).get("from") or {}).get("auto") or {}
        w = next((x for x in store.wheels(pid) if x["seq"] == auto.get("wheel")), None) if auto else None
        row = next((c for c in reversed(store.candidates(w["wheel_id"])) if c.get("release_id") == rid and c.get("recommendation")), None) if w else None
        if not row:   # D-486 덧 8 · 9: a release a person picked outside any wheel
            return sealed_picked(pid, rel, who, body)
        d = autosealed.check(main, w, row, by=who) or {"state": "none"}
        line = autosealed.line(d)
        if d.get("state") == "stopped":
            raise HTTPException(status_code=409, detail={"kind": "sealed-stopped", "message": d.get("why") if d.get("why") == autosealed.WHY_ONCE else line})
        if d.get("state") in ("passed", "failed"):   # the person still decides — the decision stays (a hold stays a hold)
            rec = dict(row["recommendation"])
            rec["sealed"] = {**d, "by": who, "at": time.time()}
            rec["criteria"] = [x for x in rec.get("criteria") or [] if x.get("name") != autosealed.ROW] + [
                {"name": autosealed.ROW, "ok": d["state"] == "passed", "text": line}]
            store.candidate_update(row["candidate_id"], recommendation=rec)
        return {"sealed": d, "line": line}

    def sealed_picked(pid, rel, who, body):
        """D-486 덧 8 · 9 (packrt-1 077): the sealed test on a release no wheel made (a model change by hand) — `reason` required, the
        base `base_release_id` or else the 운영 version, `cap_usd` (0.1 by default) the most this call may spend in real mode. The use
        is one sealed_use row and counts with the wheels' uses (autosealed.check_picked); no recommendation to keep it on."""
        reason = str(body.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail={"kind": "reason", "message": "자동 개선 밖 버전은 고른 까닭(reason)이 있어야 합니다"})
        cap = body.get("cap_usd", autosealed.CAP_USD)
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not cap > 0:
            raise HTTPException(status_code=422, detail={"kind": "cap_usd", "message": "cap_usd는 0보다 큰 USD 숫자입니다"})
        bid = str(body.get("base_release_id") or "")
        if bid:
            base = store.release(bid)
            if not base or base["project_id"] != pid:
                raise HTTPException(status_code=404, detail="기준 release가 없습니다")
        else:
            op = operational_of(pid, store.releases(pid), store.deployments())[0]
            base = store.release(op) if op else None
            if not base:
                raise HTTPException(status_code=409, detail={"kind": "sealed-base", "message": "기준 버전 없음 — base_release_id를 주거나 운영 버전이 있어야 합니다"})
        if base["id"] == rel["id"]:
            raise HTTPException(status_code=409, detail={"kind": "sealed-base", "message": "후보와 기준이 같은 버전입니다"})
        key_stop(base, main.settings.mode)   # D-492 덧 24: the base Run too
        d = autosealed.check_picked(main, rel, base, who, reason, float(cap))
        line = autosealed.line(d)
        if d.get("state") == "stopped":
            raise HTTPException(status_code=409, detail={"kind": "sealed-stopped", "message": d.get("why") if d.get("why") == autosealed.WHY_ONCE else line})
        return {"sealed": d, "line": line, "record": d.get("record")}

    def rebase_on_running(rel, base):
        """D-620: the version the wheel measured was made on a version the running server has left, by the graph alone (kglink.joined_of): the
        receiver takes a request only on the running revision, so the same option lines go on the running version as a new version. None when
        the link is off, nothing moved, or the base and the running version differ in anything else — the request then goes as before."""
        start = rel["pack"].get("from")
        running = kglink.running_revision(base)
        if not settings.kg_link or not start or running == start.get("revision"):
            return None
        origin = store.release(start.get("rid"))
        edit = store.edit(start.get("edit_id")) if start.get("edit_id") else None
        if not origin or not edit:
            return None
        target = kglink.rebase_target(running, kglink.joined_of(settings.data_dir, store.releases(rel["project_id"]), origin), edit["lines"])
        if not target:
            return None
        lines, revision, check = candidate_lines(target, [o for x in edit["lines"] for o in x["ops"]])
        if check.get("ok") is False:
            raise HTTPException(status_code=409, detail=f"같은 변경을 지금 도는 {target['name']} 위에 올릴 수 없습니다 — 요청 파일을 만들지 않았습니다")
        row = {"lines": lines, "revision": revision, "check_result": check, "node": next(iter(start.get("nodes") or []), "")}
        return make_version(rel["project_id"], target, row, {**(start.get("auto") or {}), "rebased_from": rel["id"]},
                            f"{rel['name']}의 변경을 지금 도는 {target['name']} 위로 옮김 · 측정은 {rel['name']}")

    app.state.rebase_on_running = rebase_on_running

    def note_rebase(pid, wheel_name, measured, new):
        """The wheel follows the version that goes out, and says what was measured where."""
        w = next((x for x in store.wheels(pid) if f"A-{x['seq']}" == wheel_name), None)
        if not w:
            return
        st = store.wheel_state(w["wheel_id"]) or {}
        store.set_wheel_state(w["wheel_id"], {**st, "cand": {**(st.get("cand") or {}), "version": new["id"], "measured": measured["id"]}})
        store.wheel_log(w["wheel_id"], 7, f"그래프가 앞서가 같은 변경을 {new['name']}로 옮겨 요청 · 측정은 {measured['name']}(한 그래프 앞)")

    @app.post("/api/projects/{pid}/releases/{rid}/deploy-approval", status_code=201)
    def deploy_approval(pid: str, rid: str, body: dict = Body(default_factory=dict)):
        """배포 승인 (manual p.28, CHG-113, S-4 · S-6 · S-20): an approved version only. One decision(deploy_approved) and the
        approved-change@2 request file in the LLMOps data folder; from then on the version is locked (배포 진행 중). LLMOps writes
        nothing in the Pack or deploy repositories — those sessions take the request file and report through their status files.
        EV-037 CHG-191 (D-192): a version without pack.from (registered by P04) is approved with the id of its revision's tagged Pack status
        file (a developer change, D-164) — the decision only, no request file; 409 when that file is missing, not unique, not tagged, already
        approved, or a llmops request file of that id exists."""
        p = store.project(pid)
        rel = need_pack(pid, rid)
        views = version_views(p)
        view = views.get(rid) or {}
        if view.get("rollout"):
            raise HTTPException(status_code=409, detail="이미 배포 승인한 버전입니다 — 단계는 배포 승인 창에서 봅니다")
        # D-652 12-e: this version's change already went out on the running version (D-620) — in progress or done: a second press on the
        # measured version would make one more version and approval; a failed rollout does not block
        out_on = versions.moved_out(p["releases"], views, rid)
        if out_on:
            raise HTTPException(status_code=409, detail=f"이 변경은 이미 {out_on['name']}로 옮겨 배포 승인했습니다 — 단계는 {out_on['name']}의 배포 승인 창에서 봅니다")
        aw = view.get("await")   # EV-035 CHG-186: a version the wheel made, recommended adopt and not judged yet (승인 대기)
        if view.get("state") != "승인됨" and not aw:
            raise HTTPException(status_code=409, detail=f"배포 승인은 승인됨 버전에만 합니다 — 지금 {rel['name']}은 {view.get('state') or '상태 없음'}")
        who, why = (body.get("decided_by") or "").strip(), (body.get("reason") or "").strip()
        dep = (body.get("deployment_id") or "").strip() or deploying.default_deployment_id(rel["pack"]["id"], rel["name"])
        problems = [x for x in (deploying.alias_error(who), None if why else "이유를 적어야 배포 승인이 저장됩니다",
                                # EV-067 (178 (a)): an '@dev-local' id only when a tagged ledger row has that very id (the row the deploy
                                # side switched to outside LLMOps) — an untagged dev row is still refused
                                None if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@:\-]{0,63}", dep) and (not dep.endswith("@dev-local")
                                                                                                        or deploying.target_in(dep, store.deployments()) == dep) else
                                "배포 id는 영문 · 숫자 · . _ @ : - 64자 이내이고 '@dev-local'로 끝나지 않습니다(배포단이 전환해 둔 태그 달린 같은 id 줄이 있을 때만 받음)",
                                None if versions.NAME_RE.match(rel["name"]) else "V<n> 이름의 버전만 배포 승인합니다") if x]
        if problems:
            raise HTTPException(status_code=422, detail={"kind": "form", "reasons": problems})
        if aw:   # the person's adopt is checked as a verdict would be (S-18 on the same-condition Run) before anything is written
            vr = store.run(aw["run"])
            labels = {x["uid"]: x.get("label") for x in store.runs(pid) if x["uid"] in ((view.get("judge") or {}).get("pairs") or {})}
            why_v = None if vr and vr["rel"] == rid and vr["status"] != "실행 중" and vr.get("summary") else "판정할 평가 Run이 끝나지 않았습니다"
            why_v = why_v or versions.run_gate(view.get("judge"), aw["run"], labels)
            if why_v:
                raise HTTPException(status_code=422, detail={"kind": "verdict", "reasons": [why_v]})
        base, note = running_base(rel["pack"]["id"], dep)   # EV-054 CHG-232: the row the deploy side runs now (019h, D-359)
        if note:
            raise HTTPException(status_code=409, detail=note)
        if not base:   # 019h: the ledger row takes the target's run bindings from its operational row
            raise HTTPException(status_code=409, detail=f"배포 대상 '{deploying.target_of(dep) or dep}'의 운영 배포 줄이 없어 배포 기록 줄의 자료 묶음을 정할 수 없습니다 — "
                                                         f"같은 배포 대상(예 {rel['pack']['id']}@v0-{deploying.target_of(dep) or 'pg-local'})의 운영 배포가 먼저 있어야 합니다")
        ap = view.get("approval") or ({"run": aw["run"], "label": aw.get("label")} if aw else {})
        measured = rel
        moved = rebase_on_running(rel, base)   # D-620: the graph moved under the wheel — the same option lines on the running version
        if moved:
            rel, rid = moved, moved["id"]
        start = rel["pack"].get("from")
        idx = index_evidence(rel, ap.get("run"), base)   # EV-051 CHG-W2-22 ~ 24: a vector DB this version changed (409 before anything is written)
        if not start:   # EV-037 CHG-191 (D-192): a version registered by P04 whose revision the Pack already tagged by a developer change (D-164)
            return dev_change_approval(pid, rid, rel, view, dep, who, why, base, idx)

        # the change lines from the starting revision to this version: the confirmed edit, then each '현재 버전 수정' edit on top in
        # order (review E-S-04-R1-04). An edit brought back from an earlier revision does not continue the chain: refused with the reason.
        chain = [start] + [x.get("updated_by") for x in rel["pack"].get("revisions") or [] if x.get("updated_by")] + \
            ([rel["pack"]["updated_by"]] if rel["pack"].get("updated_by") else [])
        edits, prev = [], start.get("revision")
        for o in chain:
            e = store.edit(o.get("edit_id")) if o.get("edit_id") else None
            if not e:
                continue
            if e["base_revision"] != prev:
                raise HTTPException(status_code=409, detail="이전 revision에서 가져와 고친 버전은 출발 버전에서 이어지는 변경 줄로 요청 파일을 만들 수 없습니다 — '새 버전 생성'으로 확정한 버전을 배포 승인합니다")
            edits.append(e)
            prev = e["revision"]
        lines = [l for e in edits for l in e["lines"]]
        base_rel = store.release(start.get("rid"))
        base_dir = None
        if base_rel and base_rel.get("pack"):
            base_dir = base_rel["pack"]["copy"] if base_rel["pack"].get("revision") == start.get("revision") else next(
                (x["copy"] for x in base_rel["pack"].get("revisions") or [] if x["revision"] == start.get("revision")), None)
        if not lines or not base_dir or not Path(base_dir).is_dir():
            raise HTTPException(status_code=409, detail="출발 버전의 사본이나 변경 줄을 찾을 수 없어 요청 파일을 만들 수 없습니다")
        _, revision, _, again, _ = editing.evaluate(base_dir, lines, rel["pack"].get("entrypoint"))
        if revision != rel["pack"]["revision"]:
            raise HTTPException(status_code=409, detail="출발 버전에서 이 버전까지의 변경 줄로 지금 revision을 다시 만들 수 없습니다 — 요청 파일을 만들지 않았습니다")
        if getattr(again, "moved_flow", None):   # EV-056 CHG-237 (D-376 Q-2): the Pack's apply (packrt apply_patch) refuses it — before any write
            raise HTTPException(status_code=409, detail=f"이 버전은 원래 Pack의 흐름 경고({len(again.moved_flow)}개)를 지닌 채 만든 버전이라 Pack 받아 넣기가 거절합니다 — "
                                                         "Pack이 흐름을 고친 버전(c1a15-pack v7) 뒤에 배포 승인합니다")
        change_id = f"CHG-{rel['pack']['id']}-{rel['name']}-{rel['pack']['revision'].split(':', 1)[1][:8]}"
        try:
            doc = deploying.request_doc(change_id=change_id, version=rel["name"], base_revision=start["revision"],
                                        ops=[op for l in lines for op in l["ops"]], candidate_revision=rel["pack"]["revision"],
                                        change_approval=f"verdict:{ap.get('run') or 'none'}", deploy_approval=f"deploy:{change_id}")
        except ValueError as e:
            raise HTTPException(status_code=422, detail={"kind": "request-file", "reasons": e.args[0]})
        folder = settings.data_dir / "approved-changes"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{change_id}.json"
        tmp = folder / f".{change_id}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        try:
            os.link(tmp, path)   # whole file or none, and only when it is not there yet (two requests cannot both pass)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="같은 변경의 요청 파일이 이미 있습니다")
        finally:
            tmp.unlink(missing_ok=True)
        try:
            evidence = {"release": rid, "version": rel["name"], "revision": rel["pack"]["revision"], "base_revision": start["revision"],
                        "change_id": change_id, "request": str(path), "approval": ap, "target_base": base, "model": approved_model(rel),
                        **({"index": idx} if idx else {}),
                        **({"rebased_from": {"release": measured["id"], "version": measured["name"], "revision": measured["pack"]["revision"]}} if moved else {})}
            if aw:   # one transaction: the person's adopt on the new version's same-condition Run and decision(deploy_approved) (Q-3 A)
                did = store.add_verdict_with_deploy_approval(aw["run"], {"v": "adopt", "confirm": False, "reason": why, "excl": "", "hyp": "", "by": who,
                                                                         "at": time.strftime("%m-%d %H:%M:%S"), "with_deploy": True, "auto_rec": aw["wheel"]},
                                                             deployment_id=dep, decided_by=who, reason=why, evidence={**evidence, "await": aw["wheel"]})
            else:
                did = store.add_deploy_approval(deployment_id=dep, decided_by=who, reason=why, evidence=evidence)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if moved and aw:
            note_rebase(pid, aw["wheel"], measured, rel)
        out = {"decision_id": did, "change_id": change_id, "request": str(path), "deployment_id": dep,
               "release": rid, "version": rel["name"]}   # D-652 12-e: the version that went out (moved by D-620) — the window follows it
        if ci:   # VPS: ask the Pack repository's runner (key ①); a failure never touches the decision or the request file
            try:
                out["pack_ci"] = ci.dispatch(change_id, path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as e:
                out["pack_ci"] = {"requested": False, "reason": f"요청을 보내지 않음({type(e).__name__})"}
        return out

    @app.get("/api/projects/{pid}/edit")
    def get_edit(pid: str):
        need_project(pid)
        return {"edit": edit_view(store.open_edit(pid))}

    @app.post("/api/projects/{pid}/releases/{rid}/edit/preview")
    async def edit_preview(pid: str, rid: str, request: Request):
        """Check ops on top of the open edit of this release (or on the release copy): nothing is saved (manual p.8)."""
        rel = need_pack(pid, rid)
        _, ops = await edit_body(request)
        e = store.open_edit(pid)
        mine = e if e and e["release"] == rid else None
        base = mine["base_dir"] if mine else str(pack_copy(rel))
        lines = editing.add(mine["lines"] if mine else [], ops)
        check, revision, _, res, savable = await run_in_threadpool(editing.evaluate, base, lines, rel["pack"].get("entrypoint"))
        # E-S-09: the same rule as 편집에 반영 — a node waiting for its next connection does not stop a preview (manual p.14, 32 #2)
        return {"ok": savable, "errors": check["errors"], "all_errors": res.errors, "warnings": res.warnings, "pending": check["pending"], "base_revision": res.base_revision,
                "revision": revision, "changed_files": file_hunks(base, res), "ir_diff": res.ir_diff,
                "index_rebuild": sorted(index_nodes(rel, {"lines": lines}))}   # D-492 덧 28: the retrieve@2 nodes whose vector DB the confirm makes again

    def base_values(base, rel):
        """The starting release's param values, for the change card's 'what changed' (manual p.9 · 10). Read only."""
        loaded = node_detail.load(base)
        entry = rel["pack"].get("entrypoint")
        return (lambda node, param: editing.base_value(loaded[0], entry, node, param)) if loaded else None

    @app.post("/api/projects/{pid}/releases/{rid}/edit/ops", status_code=201)
    async def edit_ops(pid: str, rid: str, request: Request):
        """'편집에 반영' (manual p.9): the ops join the Project's one edit (S-1) when the whole edit still passes the packrt checks.
        A node not connected yet is kept and only blocks confirming (p.14). Nothing is saved on a refusal."""
        rel = need_pack(pid, rid)
        body, ops = await edit_body(request)
        e = store.open_edit(pid)
        if e and e["release"] != rid:
            other = store.release(e["release"])
            raise HTTPException(status_code=409, detail={"kind": "edit-elsewhere", "release": other["name"] if other else e["release"],
                                                         "message": f"편집 중인 것이 있습니다({other['name'] if other else '?'}에서) · 확정하거나 버린 뒤 고칠 수 있습니다"})
        base = e["base_dir"] if e else str(pack_copy(rel))
        source = body.get("source") if body.get("source") in ("person", "ai") else "person"
        ai = aisuggest.line_meta(body.get("ai")) if source == "ai" else None   # EV-073 CHG-AI-9: model · prompt hash · mode of the suggestion
        try:
            lines = editing.add(e["lines"] if e else [], ops, source=source, before=base_values(base, rel), fills=body.get("fills"), ai=ai)
        except ValueError as err:
            raise HTTPException(status_code=422, detail={"kind": "check", "errors": [{"message": str(err)}]})
        check, revision, graph, res, savable = await run_in_threadpool(editing.evaluate, base, lines, rel["pack"].get("entrypoint"))
        if not savable:
            raise HTTPException(status_code=422, detail={"kind": "check", "errors": res.errors})
        if not e:
            try:
                e = store.start_edit(pid, rid, base, rel["pack"]["revision"])
            except INTEGRITY_ERRORS:
                raise HTTPException(status_code=409, detail={"kind": "edit-elsewhere", "message": "편집 중인 것이 있습니다"})
        store.save_edit(e["edit_id"], lines=lines, revision=revision, check=check, graph=graph or e.get("graph"))
        return {"edit": edit_view(store.open_edit(pid))}

    @app.delete("/api/projects/{pid}/edit/ops")
    async def edit_revert(pid: str, lines: str = Query(""), together: int = 0):
        """Revert chosen lines (manual p.20); a later line that uses what they created is reverted only together (p.34 #15)."""
        e = need_edit(pid)
        try:
            nums = sorted({int(x) for x in lines.split(",") if x.strip()})
        except ValueError:
            raise HTTPException(status_code=422, detail="되돌릴 줄 번호가 숫자가 아닙니다")
        known = {ln["n"] for ln in e["lines"]}
        if not nums or not set(nums) <= known:
            raise HTTPException(status_code=422, detail="되돌릴 줄을 고르세요")
        deps = editing.dependents(e["lines"], set(nums))
        if deps and not together:
            raise HTTPException(status_code=409, detail={"kind": "depends", "lines": deps,
                                                         "message": f"{', '.join(map(str, nums))}번을 되돌리면 {', '.join(map(str, deps))}번이 고친 node가 없어집니다"})
        rel = store.release(e["release"])
        left = editing.remove(e["lines"], set(nums) | set(deps))
        check, revision, graph, res, savable = await run_in_threadpool(editing.evaluate, e["base_dir"], left, rel["pack"].get("entrypoint"))
        if not savable:
            raise HTTPException(status_code=422, detail={"kind": "check", "errors": res.errors})
        store.save_edit(e["edit_id"], lines=left, revision=revision, check=check, graph=graph)
        return {"edit": edit_view(store.open_edit(pid))}

    @app.delete("/api/projects/{pid}/edit")
    def edit_discard(pid: str):
        """편집 버리기 (S-8): the edit ends as discarded; the release is untouched."""
        e = need_edit(pid)
        store.end_edit(e["edit_id"], "discarded")
        return {"ok": True}

    @app.get("/api/projects/{pid}/edit/diff")
    async def edit_diff(pid: str):
        """변경 내용 (manual p.21): file · lines · node · kind from the edit's starting copy to the edited Pack."""
        e = need_edit(pid)
        rel = store.release(e["release"])
        res = await run_in_threadpool(editing.check, e["base_dir"], e["lines"])
        graphs = (res.ir_diff or {}).get("graphs", {})
        changed = sorted({n for g in graphs.values() for n in (g.get("added_nodes") or []) + (g.get("removed_nodes") or []) + list(g.get("changed_nodes") or {})})
        return {"base": {"release": rel["name"], "rid": rel["id"], "revision": e["base_revision"]}, "revision": e["revision"],
                "files": [{**f, "lines": sum(max(len(h["old"]), len(h["new"])) for h in f["hunks"])} for f in file_hunks(e["base_dir"], res)],
                "nodes": changed, "ir_diff": res.ir_diff, "check": e["check"], "lines": e["lines"], "pack_diff": edit_pack_diff(e["base_dir"], res)}

    def edit_pack_diff(base_dir, res):
        """The existing revision diff (EV-002 CHG-13, packdiff) between the edit's starting copy and the edited pack.yaml (p.21)."""
        if "pack.yaml" not in (res.files or {}):
            return []
        try:
            from packrt.ir import strict_yaml
            from packrt.ir.revision import apply_defaults
            doc, _ = strict_yaml.load(res.files["pack.yaml"].decode("utf-8"))
            changes = packdiff.diff_docs(packdiff.normalized(base_dir), apply_defaults(doc))
        except Exception:   # the change view still shows the lines; this list is an aid
            return []
        return [{k: guard.redact(v, keys.known_secrets()) if k in ("before", "after") else v for k, v in c.items()} for c in changes]

    def register_folder(pid, name, folder, how_label, extra):
        """The P04 registration path for a folder LLMOps itself made (confirm, CHG-109): read with its checked execution
        config, copy, probe in the Pack environment, compare the graph — the same steps and the same keep-nothing rule."""
        result = packs.read_pack(folder, str(Path(folder) / packs.CONFIG_COPY), need_config=True, dsn=packs.runtime_dsn(settings.db_env_file))
        rel = store.add_release(pid, name, how_label, str(folder), result["code_hash"], result["graph"], [])
        try:
            out = store.add_pack_release(rel["id"], pack_record(rel["id"], folder, result, extra))
        except Exception:
            store.delete_release(rel["id"])
            raise
        return with_fixed_flow(out)

    def with_fixed_flow(rel):
        """EV-069 CHG-EF-1 (D-103): a registered Agent Pack version's fixed flow is read once at registration; an unreadable list is kept
        as '읽지 못함' and read again when the window or the start asks (fixedflow.ensure), and never stops the registration."""
        if store.auto_ready:
            try:
                fixedflow.ensure(store, rel)
            except Exception as e:   # noqa: BLE001 - the window and the start read it again
                print(f"llmops 고정 흐름 읽기 건너뜀 — {type(e).__name__}", file=sys.stderr, flush=True)
        return rel

    @app.post("/api/projects/{pid}/edit/confirm", status_code=201)
    async def edit_confirm(pid: str, body: dict = Body(default_factory=dict)):
        """확정 (manual p.22 · 43): 'new' makes the next version through the registration path; 'update' replaces the revision of
        a not-yet-deployed version (S-2, S-3, S-6), keeping the earlier copy (S-14). Locked while the edit has problems (#17)."""
        e = need_edit(pid)
        p = store.project(pid)
        rel = store.release(e["release"])
        mode, memo = body.get("mode"), (body.get("memo") or "").strip()
        if mode not in ("new", "update"):
            raise HTTPException(status_code=422, detail="저장 방법은 new(새 버전 생성) 또는 update(현재 버전 수정)입니다")
        tie = body.get("wheel")   # D-444 #10: the improvement record to tie this version to (its number), or none
        tied = next((w for w in store.wheels(pid) if w["seq"] == tie), None) if tie is not None and store.auto_ready else None
        if tie is not None and (isinstance(tie, bool) or not isinstance(tie, int) or not tied):
            raise HTTPException(status_code=422, detail="묶을 개선 기록이 없습니다")
        reasons = [x["message"] for x in e["check"].get("errors", [])] + [f"node '{n}'에 다음 연결이 필요합니다" for n in e["check"].get("pending", [])]
        if reasons or not e["revision"]:
            raise HTTPException(status_code=422, detail={"kind": "check", "reasons": reasons or ["검사를 통과하지 않았습니다"]})
        view = version_views(p).get(rel["id"]) or {}
        if mode == "update" and view.get("lock_reason"):
            raise HTTPException(status_code=409, detail={"kind": "locked", "reason": view["lock_reason"]})
        dest = settings.data_dir / "edits" / str(e["edit_id"]) / "pack"
        if dest.exists():
            shutil.rmtree(dest.parent, ignore_errors=True)
        try:
            await run_in_threadpool(editing.materialize, e["base_dir"], e["lines"], dest)
        except ValueError as err:
            raise HTTPException(status_code=422, detail={"kind": "check", "reasons": [x.get("message") for x in err.args[0]]})
        try:   # D-492 덧 28 (E1 103 (가)): the vector DB of a changed cut is made the wheel's way into the copy before it is registered
            await run_in_threadpool(main.ops.index_into, rel, e["lines"], dest, human_build)
        except packs.IndexStepError as err:
            shutil.rmtree(dest.parent, ignore_errors=True)   # nothing kept; the edit stays open
            raise HTTPException(status_code=422, detail={"kind": "index", "reasons": [f"벡터 DB를 만들지 못함 — {err}"]})
        origin = {"release": rel["name"], "rid": rel["id"], "revision": e["base_revision"], "edit_id": e["edit_id"], "memo": memo,
                  "at": time.strftime("%m-%d %H:%M"),   # the confirm time, shown on the version band (manual p.23)
                  "changes": [f"{l.get('node') + ' ' if l.get('node') else ''}{l.get('summary', '')}".strip() for l in e["lines"]],
                  # merge 3 (manual p.23 · 27, fork B lead item 10): the nodes this version changed or added, for '바뀜' on its graph
                  "nodes": sorted({l["node"] for l in e["lines"] if l.get("node")} | {n for l in e["lines"] for o in l["ops"] for n in editing.creates(o) if "/" not in n}),
                  **({"wheel": tied["seq"]} if tied else {})}   # D-444 #10: tied to A-n (no DDL — the origin)
        try:
            if mode == "new":
                out = await run_in_threadpool(register_folder, pid, versions.next_name(p["releases"]), dest, CONFIRM_LABEL, {"from": origin})
            else:
                out = await run_in_threadpool(replace_revision, rel, dest, origin)
        except Exception as err:
            shutil.rmtree(dest.parent, ignore_errors=True)   # nothing of a failed confirm is kept, whatever failed; the edit stays open
            if isinstance(err, packs.PackError):
                raise HTTPException(status_code=422, detail=err.detail())
            raise
        store.end_edit(e["edit_id"], "confirmed", out["id"], memo)
        if tied:   # D-444 #10: one line on the record's ⑤ — the person's version took its place; the record keeps its tied versions
            store.wheel_log(tied["wheel_id"], 5, f"사람 편집 {store.release(out['id'])['name']} 묶음 — 편집 #{e['edit_id']}")
            wst = store.wheel_state(tied["wheel_id"]) or {}
            wst["tied"] = [x for x in wst.get("tied") or [] if x != out["id"]] + [out["id"]]   # newest last (several: the newest is followed)
            store.set_wheel_state(tied["wheel_id"], wst)
        pos = store.layout(f"edit:{e['edit_id']}")
        if pos:
            store.set_layout(f"release:{out['id']}", pos)
        return {"release": store.release(out["id"])}

    def human_build(plan):
        """D-492 덧 28: a person's confirm may build the vector DB (its spend is that person's act — fake embedding costs nothing); a real
        build without a key name or a priced model, or over the per-build cap (automain.INDEX_USD_CAP), is refused with its reason."""
        if plan.get("mode") != "real":
            return None
        if not plan.get("key_env"):
            return automain.DROP_INDEX_NOKEY.split("버림 — ", 1)[-1]
        price = automain.embed_price(plan.get("model"))
        if price is None:
            return automain.STOP_EMBED_MODEL.format(name=plan.get("model")).split("멈춤 — ", 1)[-1]
        est = plan.get("chars", 0) * plan.get("factor", 1.0) * automain.INDEX_MARGIN * price
        return automain.DROP_INDEX_USD.split("버림 — ", 1)[-1] if est > automain.INDEX_USD_CAP else None

    def replace_revision(rel, folder, origin):
        """'현재 버전 수정' (S-2 · S-14): the new copy replaces the release copy; the earlier copy moves to
        <data>/revisions/<rid>/<short revision>/ and is listed in the release's revision history."""
        result = packs.read_pack(folder, str(Path(folder) / packs.CONFIG_COPY), need_config=True, dsn=packs.runtime_dsn(settings.db_env_file))
        old = Path(rel["pack"]["copy"])
        keep = packs.revisions_dir(settings.data_dir, rel["id"]) / rel["pack"]["revision"].split(":", 1)[1][:12]
        keep.parent.mkdir(parents=True, exist_ok=True)
        moved = not keep.exists()   # the same revision kept before (A → B → A → B): its copy is already there
        if moved:
            shutil.move(str(old), str(keep))
        else:
            shutil.rmtree(old)
        try:
            history = [x for x in rel["pack"].get("revisions") or [] if x["revision"] != rel["pack"]["revision"]] + \
                [{"revision": rel["pack"]["revision"], "code_hash": rel["code_hash"], "copy": str(keep), "until": time.time(),
                  "updated_by": rel["pack"].get("updated_by")}]   # which edit made that revision (a deploy approval follows the chain)
            pack = pack_record(rel["id"], folder, result, {"from": rel["pack"].get("from"), "revisions": history, "updated_by": origin})
        except Exception:
            if moved:
                shutil.move(str(keep), str(old))
            else:
                shutil.copytree(keep, old)
            raise
        return store.replace_pack_revision(rel["id"], code_hash=result["code_hash"], graph=result["graph"], pack=pack)

    @app.get("/api/projects/{pid}/releases/{rid}/revisions")
    def release_revisions(pid: str, rid: str):
        """Version history of one release (manual p.29, S-14): the current revision and the earlier ones kept by '현재 버전 수정'."""
        rel = need_pack(pid, rid)
        past = [{"revision": x["revision"], "until": x.get("until"), "current": False} for x in rel["pack"].get("revisions") or []]
        return {"revisions": past + [{"revision": rel["pack"]["revision"], "current": True}]}

    @app.get("/api/projects/{pid}/releases/{rid}/kg")
    def release_kg(pid: str, rid: str):
        """D-560 (dev-2 · 정답지 32쪽): the graph versions and the approvals that did not reach the graph, from the deploy side's
        signal folder (LLMOPS_KG_SIGNAL_DIR, read only) — for a Pack release's version screen."""
        need_pack(pid, rid)
        return kgsignals.read(settings.kg_signal_dir, kgcand.choices(settings.data_dir))

    @app.get("/api/projects/{pid}/releases/{rid}/kg/diff")
    def release_kg_diff(pid: str, rid: str, version: str):
        """D-581 (dev-2): a row of the graph version table against the graph before it."""
        need_pack(pid, rid)
        found, before = kgdiff.before_of(kgsignals.read(settings.kg_signal_dir)["versions"], version)
        if not found:
            raise HTTPException(404, "그래프 버전 표에 없는 버전")
        if before is None:
            raise HTTPException(409, "앞 그래프가 없음")
        try:
            return kgdiff.diff(settings.kg_pack_repo, before, version)
        except kgdiff.DiffError as e:
            raise HTTPException(409, str(e))

    def overlap_pair(report_id):
        d = kgsignals.signal(settings.kg_signal_dir, report_id)
        if d is None:
            raise HTTPException(404, "그 보고의 KG 신호가 없음")
        ps = kgsignals.pairs(d) if d.get("status") == "held" else []
        if not ps:
            raise HTTPException(409, "겹침으로 반영 안 된 보고가 아님")
        return d, ps[0]

    @app.get("/api/projects/{pid}/releases/{rid}/kg/overlap-draft")
    def release_kg_merge_draft(pid: str, rid: str, report_id: str):
        """D-560 겹침 (정답지 31쪽): the merge draft of a held overlap — a fake one (no model call; a real draft needs its approval)."""
        need_pack(pid, rid)
        return kgcand.merge_draft(overlap_pair(report_id)[1])

    @app.post("/api/projects/{pid}/releases/{rid}/kg/overlap")
    def release_kg_overlap_choice(pid: str, rid: str, body: dict = Body(...)):
        """D-560 겹침 (정답지 31쪽 · eval T7): the person's 유지 · 바꿈 · 합침 of a held overlap, once a report — 유지 = a record only;
        바꿈 · 합침 = one kg-candidate@1 file for the deploy side (deploy candidate-graph-inlet.md), base = the running graph's tag and
        this version's revision; 합침 takes the lists the person confirmed."""
        rel = need_pack(pid, rid)
        choice, report_id = body.get("choice"), str(body.get("report_id") or "")
        if choice not in ("keep", "replace", "merge"):
            raise HTTPException(422, "고르기는 유지 · 바꿈 · 합침 셋 중 하나")
        d, pair = overlap_pair(report_id)
        if report_id in kgcand.choices(settings.data_dir):
            raise HTTPException(409, "이미 고른 보고")
        cid = None
        if choice != "keep":
            try:
                rows = kgcand.replace_rows(pair) if choice == "replace" else kgcand.merge_rows(pair, {"checks": body.get("checks"), "actions": body.get("actions")})
                source = {"kind": "overlap", "records": (pair["to_section"].get("line") or {}).get("relation", {}).get("records") or [],
                          "relations": [pair["from"], pair["to"]], "wheel": None, "note": report_id}
                cid = kgcand.write(settings.data_dir, rel["pack"]["id"], {"tag": (d.get("graph") or {}).get("running_tag"), "layer": None,
                                                                            "revision": rel["pack"]["revision"]}, choice, source, kgcand.PERSON, rows)
            except kgcand.ChoiceError as e:
                raise HTTPException(422, str(e)) from None
        kgcand.record_choice(settings.data_dir, report_id, choice, cid, time.time())
        return {"choice": choice, "file_id": cid}

    @app.post("/api/projects/{pid}/releases/{rid}/revisions/{rev}/edit", status_code=201)
    def revision_to_edit(pid: str, rid: str, rev: str):
        """'편집으로 가져오기' (S-14): an edit of this release that starts from an earlier revision's copy; confirming it with
        '현재 버전 수정' brings that content back. No dedicated undo button exists."""
        rel = need_pack(pid, rid)
        if store.open_edit(pid):   # S-1 first: one edit per Project
            raise HTTPException(status_code=409, detail={"kind": "edit-elsewhere", "message": "편집 중인 것이 있습니다 · 확정하거나 버린 뒤 가져올 수 있습니다"})
        hit = next((x for x in rel["pack"].get("revisions") or [] if x["revision"] == rev or x["revision"].split(":", 1)[-1].startswith(rev)), None)
        if not hit or not Path(hit["copy"]).is_dir():
            raise HTTPException(status_code=404, detail="그 revision의 사본이 없습니다")
        try:
            e = store.start_edit(pid, rid, hit["copy"], hit["revision"])
        except INTEGRITY_ERRORS:
            raise HTTPException(status_code=409, detail={"kind": "edit-elsewhere", "message": "편집 중인 것이 있습니다"})
        check, revision, graph, _, _ = editing.evaluate(hit["copy"], [], rel["pack"].get("entrypoint"))
        store.save_edit(e["edit_id"], lines=[], revision=revision, check=check, graph=graph)
        return {"edit": edit_view(store.open_edit(pid))}

    def node_doc(pid, release):
        """(doc, entry graph) the io/context answers read: the open edit, else the given release's copy."""
        e = store.open_edit(pid)
        if e:
            rel = store.release(e["release"])
            got = editing.edited_doc(e["base_dir"], e["lines"])
        elif release:
            rel = need_pack(pid, release)
            got = node_detail.load(pack_copy(rel))
        else:
            raise HTTPException(status_code=404, detail="편집이 없습니다")
        if not got:
            raise HTTPException(status_code=422, detail="편집본 pack.yaml을 읽을 수 없습니다")
        return got[0], rel["pack"].get("entrypoint"), rel

    def state_labels(rel, doc, entry):
        """Answer 019-A E-S-03 Q3: plain names of state keys — the Pack repository's label file outside the Pack revision
        (<Pack repository>/ui-labels/state-labels.json, read only; its shape is announced later, a {key: name} object or
        {"states": {key: name}} is read), then the pack.yaml state title/description, then the key itself."""
        f = Path((origin_release(rel).get("pack") or {}).get("work_folder") or ".").parent / "ui-labels" / "state-labels.json"
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        names = {}
        if isinstance(raw, dict) and raw.get("format") == "task10-pack/state-labels@1" and raw.get("pack") == (rel.get("pack") or {}).get("id") \
                and raw.get("graph") in (None, entry):   # {"format", "pack", "graph", "labels": {key: {"label", "desc"}}}
            names = {k: (v if isinstance(v, dict) else {"label": v}) for k, v in (raw.get("labels") or {}).items() if isinstance(v, (str, dict))}
        state = (doc.get("graphs", {}).get(entry) or {}).get("state") or {}
        out = {}
        for k, tdef in state.items():
            own = tdef.get("title") or tdef.get("description") if isinstance(tdef, dict) else None
            mine = names.get(k) or {}
            out[k] = {"label": mine.get("label") or own or k, "desc": mine.get("desc"), "source": "file" if mine.get("label") else "pack" if own else "key"}
        return out

    def branch_labels(rel):
        """Answer 019-05 A (task10-pack 234912e): the label file's optional 'branches' — {router or human_review node: {case key:
        name}} — read only; {} without the file, another Pack or another shape. The screen shows the name on the branch line."""
        return labels_branches((origin_release(rel).get("pack") or {}).get("work_folder"), (rel.get("pack") or {}).get("id"))

    def label_file(work_folder, pack_id):
        """The label file next to a Pack work folder (<work>/../ui-labels/state-labels.json) of this Pack, read only; {} without it
        or in another shape."""
        f = Path(work_folder or ".").parent / "ui-labels" / "state-labels.json"
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) and raw.get("format") == "task10-pack/state-labels@1" and raw.get("pack") == pack_id else {}

    def labels_branches(work_folder, pack_id):
        """The label file's 'branches', or {} (answer 021-01 2-2: the P04 registration preview uses the same rule as the workbench)."""
        b = label_file(work_folder, pack_id).get("branches")
        return {n: {k: v for k, v in m.items() if isinstance(v, str)} for n, m in b.items() if isinstance(m, dict)} if isinstance(b, dict) else {}

    @app.get("/api/projects/{pid}/edit/nodes/{node}/io")
    def edit_node_io(pid: str, node: str, release: str = ""):
        need_project(pid)
        doc, entry, rel = node_doc(pid, release)
        io = editing.node_io(doc, entry, node)
        if io is None:
            raise HTTPException(status_code=404, detail="node가 없습니다")
        return {**io, "labels": state_labels(rel, doc, entry)}

    @app.get("/api/projects/{pid}/edit/nodes/{node}/context")
    def edit_node_context(pid: str, node: str, release: str = ""):
        need_project(pid)
        doc, entry, rel = node_doc(pid, release)
        from packrt.ir.patch import ADDABLE, editable_params
        e = store.open_edit(pid)
        folder = e["base_dir"] if e else str(pack_copy(need_pack(pid, release)))
        n = next((x for x in doc["graphs"][entry].get("nodes", []) if x.get("id") == node), None)
        form = node_detail.form(folder, doc, entry)
        options = {"editable": sorted(editable_params(doc, n)) if n else [], "providers": form["providers"], "files": form["files"]}
        store_values = {k: (v or {}).get("type") for k, v in (doc.get("stores") or {}).items()}
        ctx = editing.context_bundle(doc, entry, node, ADDABLE, options, store_values)
        if ctx is None:
            raise HTTPException(status_code=404, detail="node가 없습니다")
        return {**ctx, "labels": state_labels(rel, doc, entry)}

    # ---------------------------------------------------------------- EV-073 ✦ AI tab (D-440 · D-447, manual p.35 ~ 37)
    app.state.ai_spent = 0.0
    ai_lock = threading.Lock()

    @app.post("/api/projects/{pid}/edit/nodes/{node}/suggest")
    async def edit_node_suggest(pid: str, node: str, body: dict = Body(default_factory=dict)):
        """One node suggested by the Project's LLM (CHG-AI-5): the context bundle → gateway purpose 'ai_node' → one Pack-format node
        turned into the same change lines a person makes → checked with the edit like a preview. Nothing is saved; '편집에 반영'
        goes through edit/ops with source 'ai'. Real mode needs LLMOPS_AI_TAB_BUDGET_USD and a model of the price list (CHG-AI-6)."""
        from packrt.ir.patch import ADDABLE, editable_params
        need_project(pid)
        e = store.open_edit(pid)
        if e is None:   # like 미리보기: no edit yet → the release copy; '편집에 반영' starts the edit (manual p.9)
            rel = need_pack(pid, str(body.get("release") or ""))
            e = {"lines": [], "base_dir": str(pack_copy(rel)), "release": rel["id"]}
        elif body.get("release") and body["release"] != e["release"]:
            other = store.release(e["release"])
            raise HTTPException(status_code=409, detail={"kind": "edit-elsewhere", "release": other["name"] if other else e["release"],
                                                         "message": f"편집 중인 것이 있습니다({other['name'] if other else '?'}에서)"})
        rel = store.release(e["release"])
        doc, entry, _ = node_doc(pid, rel["id"])
        blank = next((b for b in editing.blanks(e["lines"]) if b["id"] == node), None)
        g = doc["graphs"][entry]
        cur = next((n for n in g.get("nodes") or [] if n.get("id") == node), None)
        if blank is None and cur is None:
            raise HTTPException(status_code=404, detail="node가 없습니다")
        if blank is not None and not blank["from"] and not blank.get("route"):
            raise HTTPException(status_code=409, detail={"kind": "no-input", "message": "앞 node와 먼저 이어 주세요"})
        if cur is not None and cur.get("kind") not in ADDABLE and (cur.get("kind") or "").startswith("custom_code"):
            raise HTTPException(status_code=409, detail={"kind": "function-node", "message": "함수 node — Pack 저장소에서"})
        files = node_detail.form(e["base_dir"], doc, entry)["files"]
        fixed = fixedflow.view(store.fixed_flow(rel["id"])) if store.fixed_flow(rel["id"]) else None   # read only (A2 N1)
        b = aisuggest.bundle(doc, entry, node, blank, str(body.get("ask") or "")[:2000], ADDABLE, files.get("prompts") or [], fixed, e["base_dir"])
        msgs = aisuggest.messages(b)
        provider, model = project_llm((store.project(pid) or {}).get("scoring"))
        price = autocand.chat_price(model)
        real = settings.mode != "fake"
        cap = settings.ai_tab_budget_usd
        if real:
            if cap is None:
                raise HTTPException(status_code=409, detail={"kind": "no-cap", "message": "실제 호출 상한 없음"})
            if price is None:
                raise HTTPException(status_code=409, detail={"kind": "unpriced", "message": f"가격표에 없는 모델 — {model}"})
            with ai_lock:
                if app.state.ai_spent + aisuggest.estimate_usd(msgs, price) > cap:
                    raise HTTPException(status_code=409, detail={"kind": "cap", "message": f"멈춤 · 비용 상한({app.state.ai_spent:.4f} / {cap} 달러)"})
        try:
            out = await run_in_threadpool(app.state.gateway.chat, msgs, "ai_node", provider, model, 60, True)   # D-502 (1): fake mode, no key needed
        except NoKey as err:
            raise HTTPException(status_code=422, detail={"kind": "no-key", "message": str(err)})
        except Exception as err:  # noqa: BLE001 - a provider failure is shown as code · where · HTTP status only (CHG-AI-7)
            raise HTTPException(status_code=502, detail={"kind": "provider", "code": type(err).__name__, "where": "ai_node", "status": getattr(err, "code", None)})
        usd = aisuggest.usage_usd(out.get("usage"), price) if real and price else 0.0
        with ai_lock:
            app.state.ai_spent += usd
        answer = aisuggest.parse(out["text"])
        calls = {}
        for row in list(app.state.gateway.log):
            if row.get("mode") == settings.mode:
                calls[row.get("purpose")] = calls.get(row.get("purpose"), 0) + 1
        meta = {"model": out.get("served_model") or model, "mode": settings.mode, "usage": out.get("usage"), "usd": round(usd, 6),
                "spent": round(app.state.ai_spent, 6), "cap": cap, "server_calls": calls, "ai": {"model": model, "prompt_sha": aisuggest.prompt_sha(msgs), "mode": settings.mode}}
        if answer is None:
            return {"proposal": None, "why": None, "dropped": ["버림 — 답을 읽지 못함"], "check": None, "fixed_flow": [], "out_choices": [], **meta}
        editable = sorted(editable_params(doc, cur)) if cur is not None else []
        ops, pnode, dropped, outs = aisuggest.to_ops(b, answer, doc, entry, blank, ADDABLE, editable)
        check, fixed_hits = None, []
        if ops:
            lines = editing.add(e["lines"], ops, source="ai", fills=node if blank is not None else None)
            check, _, _, _, _ = await run_in_threadpool(editing.evaluate, e["base_dir"], lines, entry)
            if fixed and fixed.get("items"):
                def touched(ls):   # the fixed-flow items an edited copy breaks; a copy packrt cannot load yet (a waiting node) breaks none
                    tmp = Path(tempfile.mkdtemp(prefix="llmops-ai-"))
                    try:
                        editing.check(e["base_dir"], ls, tmp / "pack")
                        return [v for v in fixedflow.check(fixed["items"], tmp / "pack") if v.get("item") != "load"] if (tmp / "pack").is_dir() else []
                    except Exception:  # noqa: BLE001 - the touch mark is shown only
                        return []
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                before_hits = {v.get("what") for v in (await run_in_threadpool(touched, e["lines"]) if e["lines"] else [])}
                # only what this suggestion touches — not what the edit already touched before it
                fixed_hits = [{"what": v.get("what"), "message": v.get("message")} for v in await run_in_threadpool(touched, lines) if v.get("what") not in before_hits]
        return {"proposal": {"ops": ops, "node": pnode, "fills": node if blank is not None else None} if ops else None, "why": str(answer.get("why") or "")[:300],
                "dropped": dropped, "check": check, "fixed_flow": fixed_hits, "out_choices": outs, **meta}

    @app.post("/api/projects/{pid}/edit/delete-impact")
    async def edit_delete_impact(pid: str, body: dict = Body(default_factory=dict)):
        """What removing a node or one edge from the open edit would do (manual p.17 · 18 · 34, CHG-117): packrt's delete_impact
        on a temporary copy of the edited Pack. Nothing is stored — to remove, the screen sends the returned ops to edit/ops."""
        from packrt.ir.patch import delete_impact
        e = need_edit(pid)
        rel = store.release(e["release"])
        try:
            folder, cleanup = await run_in_threadpool(editing.temp_copy, e["base_dir"], e["lines"])
        except ValueError as err:   # an edit that does not pass yet (e.g. a node waiting for its next connection)
            raise HTTPException(status_code=409, detail={"kind": "edit-not-ready", "reasons": [x.get("message") for x in err.args[0]]})
        try:
            out = await run_in_threadpool(delete_impact, folder, rel["pack"].get("entrypoint"), node=body.get("node"), edge=body.get("edge"))
            after = out.get("after") or {}
            if out.get("ops") and any(x.get("stage") == "flow" for x in after.get("errors") or []):   # EV-056 CHG-236: the same rule
                after["ok"], after["errors"] = await run_in_threadpool(editing.recheck_after, e["base_dir"], folder, out["ops"])
            return out
        finally:
            cleanup()

    @app.get("/api/projects/{pid}/prompt-library")
    def prompt_library(pid: str):
        """The prompt library outside the Pack revision (S-5, manual p.13): task10-pack/prompt-library (index.json format
        task10-pack/prompt-library@1 and its files), read only. Choosing one sends a create_file op (the file at its pack_path) with
        the node's prompt in one request to edit/ops. A missing folder gives an empty list with a note."""
        need_project(pid)
        folder = Path(settings.prompt_library or REPO_ROOT.parent / "task10-pack" / "prompt-library")
        if not folder.is_dir():
            return {"items": [], "note": "묶음 없음 — task10-pack 준비 중", "format": None}
        try:
            doc = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            raise HTTPException(status_code=422, detail=f"프롬프트 묶음 index.json을 읽을 수 없습니다 — {type(err).__name__}")
        if not isinstance(doc, dict) or doc.get("format") != "task10-pack/prompt-library@1" or not isinstance(doc.get("prompts") or [], list) \
                or not all(isinstance(p, dict) for p in doc.get("prompts") or []):
            raise HTTPException(status_code=422, detail=f"프롬프트 묶음 형식이 다릅니다 — {doc.get('format') if isinstance(doc, dict) else type(doc).__name__}")
        root, items = folder.resolve(), []
        for p in doc.get("prompts") or []:
            f = (folder / str(p.get("file") or "")).resolve()
            try:
                if f.parent != root or not f.is_file():
                    raise OSError
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raise HTTPException(status_code=422, detail=f"프롬프트 묶음의 파일을 읽을 수 없습니다 — {p.get('file')}")
            items.append({"id": p.get("id"), "name": p.get("title") or p.get("id"), "file": p.get("file"), "path": p.get("pack_path"),
                          "kind": p.get("kind"), "reads": p.get("reads") or [], "writes": p.get("writes") or [], "output": p.get("output"),
                          "place": p.get("place"), "source": p.get("source"), "text": guard.redact(text, keys.known_secrets())})
        return {"items": items, "note": None, "format": doc["format"]}

    def layout_owner(pid, rid=None):
        if rid:
            need_pack(pid, rid)
            return f"release:{rid}"
        return f"edit:{need_edit(pid)['edit_id']}"

    @app.get("/api/projects/{pid}/releases/{rid}/layout")
    def get_release_layout(pid: str, rid: str):
        pos = store.layout(layout_owner(pid, rid))
        return {"positions": pos or {}, "saved": pos is not None}

    @app.put("/api/projects/{pid}/releases/{rid}/layout")
    def put_release_layout(pid: str, rid: str, body: dict = Body(default_factory=dict)):
        store.set_layout(layout_owner(pid, rid), check_positions(body))
        return {"ok": True}

    @app.get("/api/projects/{pid}/edit/layout")
    def get_edit_layout(pid: str):
        pos = store.layout(layout_owner(pid))
        return {"positions": pos or {}, "saved": pos is not None}

    @app.put("/api/projects/{pid}/edit/layout")
    def put_edit_layout(pid: str, body: dict = Body(default_factory=dict)):
        store.set_layout(layout_owner(pid), check_positions(body))
        return {"ok": True}

    def check_positions(body):
        pos = body.get("positions") if isinstance(body, dict) else None
        if not isinstance(pos, dict) or len(pos) > 500 or not all(isinstance(k, str) and isinstance(v, list) and len(v) == 2
                                                                  and all(isinstance(x, (int, float)) for x in v) for k, v in pos.items()):
            raise HTTPException(status_code=422, detail="positions는 {node: [x, y]} 모양이어야 합니다")
        return pos

    @app.post("/api/projects/{pid}/eval-suites", status_code=201)
    def add_suite(pid: str, body: NewSuite):
        """Register evals/<suite>/ of a Pack release's work folder as a run input (EV-002 CHG-11): checked, copied into the
        data dir (the work folder is only read), then fixed by suite_revision and dataset_hash."""
        need_project(pid)
        rel = store.release(body.release)
        if not rel or rel["project_id"] != pid or not rel.get("pack"):
            raise HTTPException(status_code=422, detail="평가 suite는 Agent Pack release에서만 등록할 수 있습니다")
        try:
            review_nodes = [n["name"] for n in (rel.get("graph") or {}).get("nodes", []) if str(n.get("kind", "")).startswith("human_review")]
            su = suites.read_suite(rel["pack"]["work_folder"], body.suite.strip(), rel["pack"], review_nodes)
        except (suites.SuiteError, OSError) as e:
            raise HTTPException(status_code=422, detail=f"평가 suite를 등록할 수 없습니다 — {e}")
        src = su.pop("source")
        return register_suite(pid, su, src)

    def register_suite(pid, su, src):
        """Shared by the evals/ registration and the 올린 case suite (OPS-3): same content once per Project, copy, hash check."""
        rec, same = store.add_suite_unique(pid, su)
        if same:   # EV-012 CHG-69: the same content is registered once per Project; name the earliest registration
            raise HTTPException(status_code=409, detail=f"이미 같은 내용의 평가 suite가 등록돼 있습니다 — {same['name']} · {suite_tag(store.suites(pid), same)} "
                                                        "(같은 Pack·suite_revision·dataset_hash)")
        try:
            dest = suites.copy_suite(src, settings.data_dir, rec["id"])
            if "sha256:" + dir_hash(dest, exclude=()) != su["suite_revision"]:
                raise OSError("사본이 읽은 내용과 다릅니다(등록 중 바뀌었을 수 있음)")
        except Exception as e:  # noqa: BLE001 - a failed copy keeps nothing
            store.delete_suite(rec["id"])
            d = suites.copy_dir(settings.data_dir, rec["id"])
            if d:
                shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"평가 suite를 등록할 수 없습니다 — {e}")
        return store.set_suite_copy(rec["id"], dest)

    @app.post("/api/projects/{pid}/input-sets/generate")
    def generate_input_set(pid: str, body: GenerateInputs):
        """Input generation with the LLMOps key (P-09): rows in the release's input shape; the rows that fit are kept as an input
        set without a separate save step; nothing is kept when none fits. No key → nothing is generated."""
        p = need_project(pid)
        rel = store.release(body.release)
        if not rel or rel["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        if rel["sourceKind"] == "agent_pack":   # EV-002 CHG-15
            raise HTTPException(status_code=422, detail="Agent Pack release는 입력값 생성을 아직 지원하지 않습니다 — 평가 suite·파일·직접 쓰기를 쓰세요")
        try:
            prov, model = project_llm(p["scoring"])   # the Project's provider only (EV-005 CHG-28, D-5)
            rows = gatewaymod.generate_inputs(app.state.gateway, rel, body.count, guard.redact(body.desc.strip(), keys.known_secrets()), prov, model)
        except NoKey as e:
            raise HTTPException(status_code=422, detail=f"{e} — 생성할 수 없습니다")
        ok = [r["value"] for r in rows if r["ok"]]
        s = store.add_input_set(pid, f"생성 · {rel['name']} · {time.strftime('%m-%d %H:%M:%S')}", "생성됨", ok, rel["name"], settings.mode_label) if ok else None
        return {"rows": rows, "set": s, "count": body.count, "release": rel["id"], "desc": guard.redact(body.desc, keys.known_secrets())}

    @app.post("/api/runs/{uid}/verdicts", status_code=201)
    def save_verdict(uid: str, body: NewVerdict):
        """A person's verdict (P-08): a record only — no code change, deploy or external action happens here."""
        r = need_run(uid)
        if r.get("candidate"):   # EV-022 S-15: a Run of the removed candidate feature is an earlier-flow record, never judged
            raise HTTPException(status_code=422, detail="흐름 이전 기록 Run에는 판정을 남기지 않습니다")
        if r["status"] == "실행 중" or not r.get("summary"):
            raise HTTPException(status_code=409, detail="실행이 끝나면 판정할 수 있습니다")
        rel = store.release(r["rel"])
        view = (version_views(store.project(r["project_id"])).get(r["rel"]) or {}) if rel and rel.get("pack") else {}
        if view.get("state") in ("승인됨", "거절"):   # answer 019-A E-S-03 G1: the result stands; a change goes through edit → confirm (p.27 · 43)
            raise HTTPException(status_code=409, detail=f"{rel['name']}은 {view['state']} 버전이라 판정을 더 남기지 않습니다 — 바꾸려면 {rel['name']}에서 새 편집 → 확정")
        if body.by.strip() and deploying.alias_error(body.by.strip()):
            raise HTTPException(status_code=422, detail=deploying.alias_error(body.by.strip()))
        if body.v in ("adopt", "reject"):   # EV-022 S-7 · S-18: a confirmed version is judged on a same-condition evaluation
            gate = view.get("judge") or {}
            labels = {x["uid"]: x.get("label") for x in store.runs(r["project_id"]) if x["uid"] in (gate.get("pairs") or {})}
            why = versions.run_gate(gate, uid, labels)
            if why:
                raise HTTPException(status_code=422, detail=why)
        rec = versions.recommendation(r, view.get("judge")).get("v")   # S-22: a same-condition evaluation with moved paths → hold
        confirm = bool(rec) and body.v == rec
        if not confirm and not body.reason.strip():
            raise HTTPException(status_code=422, detail="권고와 다른 판정은 이유를 적어야 저장됩니다." if rec else "권고가 없는 Run은 이유를 적어야 저장됩니다.")
        store.add_verdict(uid, {"v": body.v, "confirm": confirm, "reason": body.reason.strip(), "excl": body.excl.strip(), "hyp": body.hyp.strip(),
                                **({"by": body.by.strip()} if body.by.strip() else {}),
                                "at": time.strftime("%m-%d %H:%M:%S")})
        return run_view(need_run(uid))

    @app.get("/api/runs/{uid}/events")
    async def run_events(uid: str, request: Request):
        """Server-sent events read from the stored event log (the record), so the live view and the record are one source."""
        need_run(uid)
        after = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

        async def gen():
            nonlocal after
            while True:
                for ev in store.events(uid, after):
                    after = ev["seq"]
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev["type"] == "run_end":
                        return
                if store.run(uid)["status"] != "실행 중" and not store.events(uid, after):
                    yield "event: end\ndata: {}\n\n"
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/settings")
    def get_settings():
        return settings_view()

    KEY_ORIGIN = "다른 사이트의 요청으로는 키를 바꿀 수 없습니다"

    async def read_key(request: Request, provider: str, empty: str):
        # parsed by hand so that no validation error can echo the submitted value back
        own_origin(request, "key-origin", KEY_ORIGIN)
        json_request(request, "key-request")
        try:
            body = await request.json()
            keys.set_temp(body.get("value") if isinstance(body, dict) and isinstance(body.get("value"), str) else "", provider)
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=422, detail=empty)
        return settings_view()

    def need_provider(provider):
        if provider not in PROVIDERS:
            raise HTTPException(status_code=404, detail="모르는 provider입니다")

    @app.post("/api/settings/keys/{provider}")
    async def set_provider_key(provider: str, request: Request):
        """EV-005 CHG-25: a temporary key for one provider (memory only)."""
        need_provider(provider)
        return await read_key(request, provider, "키를 입력하세요")

    @app.delete("/api/settings/keys/{provider}")
    def clear_provider_key(provider: str, request: Request):
        need_provider(provider)
        own_origin(request, "key-origin", KEY_ORIGIN)   # no body: only the Origin rule applies
        keys.clear_temp(provider)
        return settings_view()

    @app.post("/api/settings/temp-key")
    async def set_temp_key(request: Request):
        """Compatibility: the OpenAI temporary key, under the same request rules as the provider key API (EV-005)."""
        return await read_key(request, "openai", "임시 키를 넣으세요")

    @app.delete("/api/settings/temp-key")
    def clear_temp_key(request: Request):
        own_origin(request, "key-origin", KEY_ORIGIN)
        keys.clear_temp("openai")
        return settings_view()

    async def fake_completions(request: Request, run=None, case=None):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="JSON body required")
        try:
            return fake.complete(body if isinstance(body, dict) else {}, run=run, case=case, key_present=bool(request.headers.get("authorization")))
        except FakeFault as e:
            raise HTTPException(status_code=500, detail=str(e))
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))
        except gatewaymod.FakeSchemaError as e:
            raise HTTPException(status_code=400, detail=str(e))

    async def fake_embeddings(request: Request, run=None, case=None):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="JSON body required")
        try:
            return fake.embed(body if isinstance(body, dict) else {}, run=run, case=case, key_present=bool(request.headers.get("authorization")))
        except gatewaymod.FakeSchemaError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/fake/v1/embeddings")
    async def fake_embed(request: Request):
        return await fake_embeddings(request)

    @app.post("/fake/v1/{run}/{case}/embeddings")
    async def fake_embed_scoped(run: str, case: str, request: Request):
        return await fake_embeddings(request, run, case)

    @app.post("/fake/v1/chat/completions")
    async def fake_chat(request: Request):
        return await fake_completions(request)

    async def fake_body(request: Request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="JSON body required")
        return body if isinstance(body, dict) else {}

    # the Anthropic / Gemini request formats on the fake provider, for real-mode tests on this machine (EV-005 CHG-29)
    @app.post("/fake/anthropic/v1/messages")
    async def fake_anthropic(request: Request):
        body = await fake_body(request)
        try:
            return fake.complete_anthropic(body, key_present=bool(request.headers.get("x-api-key")))
        except FakeFault as e:
            raise HTTPException(status_code=500, detail=str(e))
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))

    @app.post("/fake/gemini/v1beta/models/{model}:generateContent")
    async def fake_gemini(model: str, request: Request):
        body = await fake_body(request)
        try:
            return fake.complete_gemini(model, body, key_present=bool(request.headers.get("x-goog-api-key")))
        except FakeFault as e:
            raise HTTPException(status_code=500, detail=str(e))
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))

    @app.post("/fake/v1/{run}/{case}/chat/completions")
    async def fake_chat_scoped(run: str, case: str, request: Request):
        return await fake_completions(request, run, case)

    def parse_validation(f: FileUpload):
        try:
            return parse_file(f.name, f.text, need_expected=True)
        except ParseError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/api/parse/validation-data")
    def preview_validation(f: FileUpload):
        cases = parse_validation(f)
        return {"name": f.name, "cases": cases}

    # D-479 덧 2 (packrt 156 F2): /api/state requests that come together share one reading — a request joins the one under way when no
    # write (a request other than GET · HEAD · OPTIONS) began or ended since that one began; else it reads again. Nothing is kept once
    # it ends. Overlapping or dropped requests no longer each read and write the whole answer (8 at once took 8 times as long).
    writes, flight = [0], {"lock": threading.Lock(), "now": None}

    class CountWrites:   # plain ASGI (a streamed answer passes through untouched)
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
                return await self.app(scope, receive, send)
            writes[0] += 1
            try:
                await self.app(scope, receive, send)
            finally:
                writes[0] += 1
    app.add_middleware(CountWrites)

    @app.get("/api/state")
    def state(request: Request):
        gen = writes[0]
        with flight["lock"]:
            f = flight["now"]
            mine = not (f and f["gen"] == gen)
            if mine:
                f = flight["now"] = {"gen": gen, "done": threading.Event(), "body": None, "pack": threading.Lock(), "gz": None}
        if not mine:
            f["done"].wait()
            if f["body"] is None:   # it failed: read alone
                f = {"body": state_answer().body, "pack": threading.Lock(), "gz": None}
            return state_sent(request, f)
        try:
            f["body"] = state_answer().body
        finally:
            with flight["lock"]:
                if flight["now"] is f:
                    flight["now"] = None
            f["done"].set()
        return state_sent(request, f)

    def state_sent(request, f):
        # D-479 덧 7 · 8 (packrt 161 G3): a 50 ~ 86 MB answer sent to several clients at once made macOS refuse network memory (ENOBUFS)
        # and asyncio closed the connection mid-body without a log line — gzip level 1 when the client takes it (browsers do), about 11
        # times smaller, made here in the worker thread (GZipMiddleware would pack on the event loop) and once per shared reading
        if "gzip" not in (request.headers.get("accept-encoding") or "").lower():
            return Response(f["body"], media_type="application/json", headers={"Vary": "Accept-Encoding"})
        with f["pack"]:
            if f["gz"] is None:
                f["gz"] = gzip.compress(f["body"], 1)
        return Response(f["gz"], media_type="application/json", headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})

    def state_answer():
        projects = store.projects()
        memo = {}   # EV-052: one memo for this answer — project_elsewhere and version_views share approval_progress · rollbacks
        # D-470 덧 21 (B-2): and the stored Runs · releases · names this answer read, never read again for the version views
        memo.update(runs={p["id"]: p["runs"] for p in projects}, names={p["id"]: p["name"] for p in projects},
                    run={r["uid"]: r for p in projects for r in (*p["runs"], *p["legacy"]["runs"])},
                    all_releases=[dict(r) for p in projects for r in p["releases"]])
        # state-n1 (D-463 덧 17): and the deployments · open edits · releases by Project and id, read once for the answer
        memo.update(deployments=store.deployments(), edits=store.open_edits() if hasattr(store, "open_edits") else None,
                    by_pid={p["id"]: [dict(r) for r in p["releases"]] for p in projects})
        memo["rel_by_id"] = {r["id"]: r for rs in memo["by_pid"].values() for r in rs}
        for p in projects:
            # EV-030 CHG-156: the '✦ 자동 개선' tab — a Project with an Agent Pack release, while the auto tables exist (V-DIAG-OFF)
            p["auto"] = {"on": bool(store.auto_ready and any(r.get("pack") for r in p["releases"]) and not project_elsewhere(p["id"], memo))}   # D-168
            p["runs"] = [run_view(r) for r in p["runs"]]
            views = version_views(p, memo, with_base=True)
            for rel in p["releases"]:
                if rel["id"] in views:
                    rel["version"] = views[rel["id"]]
                if rel.get("pack"):   # answer 019-05 A: the branch names of the label file, for the graph's branch lines
                    rel["branchLabels"] = branch_labels(rel)
                    rel["inputSummary"] = ops_input(rel)   # D-432 덧 7: the Run tab case list reads the alarm words of the operational list (F-06)
            e = memo["edits"].get(p["id"]) if memo.get("edits") is not None else store.open_edit(p["id"])
            if e:
                pend = e["check"].get("pending", [])
                errs = [x["message"] for x in e["check"].get("errors", []) if not (pend and (any(r.match(x.get("message") or "") for r in editing.PENDING) or editing.FOLLOWS_PENDING.match(x.get("message") or "")))]
                # a node waiting for its next connection is the grey '다음 연결 필요' on the band, not a problem (manual p.14 · 31 #5); confirming stays locked
                p["edit"] = {"release": e["release"], "lines": len(e["lines"]), "ok": not errs and not pend and bool(e["revision"]), "problems": errs, "pending": pend,
                             "next_name": versions.next_name(p["releases"]), "revision": e["revision"]}
                # D-444 #10 (E3 way A): the improvement records on the edit's base version, newest first; the default is the newest that
                # made no version of its own (handed to a person)
                ws = sorted([w for w in store.wheels(p["id"]) if w["base_release_id"] == e["release"]], key=lambda w: -w["seq"]) if store.auto_ready else []
                own = lambda w: ((store.wheel_state(w["wheel_id"]) or {}).get("cand") or {}).get("version")
                p["edit"].update(wheels=[{"seq": w["seq"], "name": f"A-{w['seq']}"} for w in ws], wheel_default=next((w["seq"] for w in ws if not own(w)), None))
            else:
                p["edit"] = None
            p["legacy"] = {"runs": [run_view(r) for r in p["legacy"]["runs"]],   # earlier-flow records (S-15): shown folded only
                           "decisions": [{k: d[k] for k in ("decision_id", "decision", "reason", "decided_by", "decided_at")} for d in p["legacy"]["decisions"]]}
        last = store.last_decided_by()   # D-456: the decider alias fields start with it (an alias only)
        # D-470 덧 22 (보고 070 덧 4 (A)): pure JSON, written once as JSONResponse renders — FastAPI's jsonable_encoder walked every value of
        # a 53 MB answer (≈ 2 s on the 8793 copy) to give the same bytes
        return JSONResponse({"projects": projects, "last_by": last if last and not deploying.alias_error(last) else None})

    @app.post("/api/projects", status_code=201)
    def create_project(body: NewProject):
        scoring = {"skip": True, "rule": False, "llm": False} if body.scoring.skip else {"skip": False, "rule": body.scoring.rule, "llm": body.scoring.llm}
        if not scoring["skip"] and not (scoring["rule"] or scoring["llm"]):
            raise HTTPException(status_code=422, detail="채점 방식을 고르세요")
        if scoring["llm"] and (body.scoring.llm_provider or body.scoring.llm_model):
            # EV-005 CHG-27: the provider and model of LLM scoring, kept when given (the screen always sends them). Without
            # them the stored scoring keeps its old shape and the Project uses OpenAI and its default model (D-5).
            prov = body.scoring.llm_provider or "openai"
            if prov not in PROVIDERS:
                raise HTTPException(status_code=422, detail="모르는 provider입니다")
            model = (body.scoring.llm_model or "").strip() or PROVIDERS[prov]["model"]
            if not MODEL_RE.match(model):
                raise HTTPException(status_code=422, detail="모델 이름에 쓸 수 없는 문자가 있습니다")
            scoring.update(llm_provider=prov, llm_model=model)
        data = {"name": body.data.name, "cases": parse_validation(body.data)} if body.data else None
        return store.create_project(body.name.strip(), body.desc, scoring, {"pass": body.rule.pass_, "below": body.rule.below}, data)

    def need_project(pid):
        p = store.project(pid)
        if not p:
            raise HTTPException(status_code=404, detail="Project가 없습니다")
        return p

    def read_source(src: ReleaseSource, keep_upload: bool):
        """Read a release source; returns (folder, path label, read result, excluded .env files). Raises 422 with the reason."""
        excluded, staging, kept = [], None, None
        try:
            if src.how == "local":
                folder, label = graphread.check_folder(src.path.strip()), src.path.strip()
                if packs.is_pack(folder):   # Agent Pack: checked by packrt.ir, never run here (EV-002 CHG-01)
                    if any(ch in str(folder) for ch in '"\\\n'):
                        raise graphread.ReadError("path-invalid", str(folder))
                    return folder, label, packs.read_pack(folder, src.config.strip() or None, need_config=keep_upload,
                                                          dsn=packs.runtime_dsn(settings.db_env_file)), []
            elif src.how == "builtin":
                known = {b["id"] for b in builtin_releases()}
                if src.builtin not in known:
                    raise HTTPException(status_code=422, detail="내장 예시를 고르세요")
                folder, label = EXAMPLES_DIR / src.builtin, ""
            else:
                files = {k: base64.b64decode(v) for k, v in (src.files or {}).items()}
                if not files:
                    raise graphread.ReadError("upload-empty")
                root = settings.data_dir / ("uploads" if keep_upload else "staging")
                folder, excluded = guard.copy_upload(strip_common_root(files), root)
                staging, kept, label = (None, folder, "") if keep_upload else (folder, None, "")
            if packs.is_pack(folder):   # uploaded or built-in folder holding pack.yaml (EV-002 CHG-01, D-14)
                packs.check_coexist(folder)
                raise packs.PackError("pack-local-only", "등록 안 됨 — Agent Pack은 지금 로컬 경로 등록만 지원합니다")
            result = graphread.read_release(folder, write_deny=[settings.data_dir])
        except packs.PackError as e:
            if kept:
                shutil.rmtree(kept, ignore_errors=True)
            raise HTTPException(status_code=422, detail=e.detail())
        except graphread.ReadError as e:
            if kept:
                shutil.rmtree(kept, ignore_errors=True)   # a failed registration keeps nothing
            raise HTTPException(status_code=422, detail={"kind": e.kind, "message": str(e)})
        except ValueError as e:
            raise HTTPException(status_code=422, detail={"kind": "upload-path", "message": str(e)})
        finally:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
        return folder, label, result, excluded

    @app.get("/api/builtin-releases")
    def builtin_list():
        return builtin_releases()

    def chooser(what: str, request: Request):
        """EV-003 CHG-17/18: the macOS chooser opens only for a JSON request from this server's own page."""
        json_request(request, "pick-request")
        own_origin(request, "pick-origin", "다른 사이트의 요청으로는 선택 창을 열 수 없습니다")
        try:
            return picker.pick(what, settings.fake_pick)
        except picker.PickError as e:
            raise HTTPException(status_code=e.status, detail=e.detail())

    @app.post("/api/pick-folder")
    def pick_folder(request: Request, body: dict = Body(default_factory=dict)):
        return chooser("folder", request)

    @app.post("/api/pick-file")
    def pick_file(request: Request, body: dict = Body(default_factory=dict)):
        return chooser("file", request)

    @app.post("/api/projects/{pid}/releases/read")
    def read_release(pid: str, src: ReleaseSource):
        need_project(pid)
        _, label, result, excluded = read_source(src, keep_upload=False)
        out = {"path": label, "envExcluded": excluded, **result}
        if result.get("sourceKind") == "agent_pack":   # answer 021-01 2-2: branch names in the P04 preview as on the workbench
            out["branchLabels"] = labels_branches((result.get("pack") or {}).get("work_folder"), (result.get("pack") or {}).get("id"))
        return out

    @app.post("/api/projects/{pid}/releases", status_code=201)
    def register_release(pid: str, body: NewRelease):
        need_project(pid)
        folder, label, result, excluded = read_source(body, keep_upload=True)
        path = label if body.how == "local" else str(folder)
        rel = store.add_release(pid, body.name.strip(), HOW_LABEL[body.how], path, result["code_hash"], result["graph"], excluded)
        if result.get("sourceKind") != "agent_pack":
            return rel
        try:
            return with_fixed_flow(store.add_pack_release(rel["id"], pack_record(rel["id"], folder, result)))
        except packs.PackError as e:   # any failure keeps nothing: no release row, no copy (CHG-07)
            store.delete_release(rel["id"])
            raise HTTPException(status_code=422, detail=e.detail())

    def pack_record(rid, folder, result, extra=None):
        """The unchangeable copy the Runs will use (EV-002 CHG-02), probed in the Pack environment (sandboxed, nothing runs) and
        compared with pack.yaml (CHG-07, CHG-08). Returns the Pack record to store; on any failure the copy is gone and a
        PackError explains why. Shared by registration (P04) and confirming an edit (EV-022 CHG-109)."""
        try:
            dest = packs.make_copy(folder, settings.data_dir, rid, result["pack"])
        except OSError as e:
            raise packs.PackError("pack-copy", f"사본을 만들 수 없습니다 — {e}")
        try:
            proj = packs.probe(settings.data_dir, result["pack"], dest, keys.known_secrets())
            routes = packs.on_error_routes(packs.ir.load_pack(dest).doc or {}, result["graph"]["graph_name"])   # EV-045: compared, never stored
            diff = packs.compare_projection(result["graph"], result["pack"], proj, routes)
            if diff:
                raise packs.PackError("pack-mismatch", guard.redact("등록 안 됨 — pack.yaml 그래프와 Pack 환경이 만든 그래프가 다릅니다 — " + " / ".join(diff), keys.known_secrets()))
            kept = {k: v for k, v in result["pack"].items() if k != "configs"}   # the folder's config list is a read-time aid only
            return {**kept, "copy": str(dest), "runtime": proj["runtime"], **(extra or {})}
        except Exception as e:
            shutil.rmtree(dest, ignore_errors=True)
            if isinstance(e, packs.PackError):
                raise
            raise packs.PackError("pack-probe", guard.redact(f"등록 안 됨 — Pack 환경 확인 중 오류 — {type(e).__name__}: {e}", keys.known_secrets())[:400])

    @app.post("/api/projects/{pid}/releases/{rid}/reread")
    def reread_release(pid: str, rid: str):
        """Opening a release re-reads a local-path release so the run flow follows the current code (AC-08)."""
        need_project(pid)
        r = store.release(rid)
        if not r or r["project_id"] != pid:
            raise HTTPException(status_code=404, detail="release가 없습니다")
        if r["sourceKind"] == "agent_pack":   # runs use the copy; only report whether the work folder moved on (CHG-02)
            changed = packs.work_changed(r["pack"])
            # answer 019-A merge 3 §5: a work folder that now holds another release of this Project (e.g. V1 after its deploy) is
            # named, so the screen does not ask to register the same content again (D-50: tag/commit based later)
            rev = packs.work_revision(r["pack"]) if changed else None
            same = next((x for x in store.releases(pid) if x["id"] != rid and (x.get("pack") or {}).get("revision") == rev), None) if rev else None
            return {**r, "workChanged": changed, "workSameAs": same and same["name"]}
        if r["how"] != HOW_LABEL["local"]:
            return r
        try:
            result = graphread.read_release(r["path"], write_deny=[settings.data_dir])
        except graphread.ReadError as e:
            return {**r, "readError": str(e)}
        if result["code_hash"] != r["code_hash"]:
            r = store.update_release_code(rid, result["code_hash"], result["graph"])
        return r

    @app.get("/api/examples")
    def examples():
        return [{"name": e["name"], "desc": e["desc"], "releases": [r["name"] for r in e["releases"]],
                 "cases": len(json.loads((EXAMPLES_DIR / e["key"] / e["validation"]["file"]).read_text()))} for e in load_examples()]

    @app.post("/api/examples/{name}", status_code=201)
    def create_example(name: str):
        ex = next((e for e in load_examples() if e["name"] == name), None)
        if not ex:
            raise HTTPException(status_code=404, detail="예시가 없습니다")
        folder = EXAMPLES_DIR / ex["key"]
        cases = parse_file(ex["validation"]["name"], (folder / ex["validation"]["file"]).read_text(), need_expected=True)
        reads = []
        for r in ex["releases"]:
            try:
                reads.append((r, graphread.read_release(folder / r["dir"], write_deny=[settings.data_dir])))
            except graphread.ReadError as e:
                raise HTTPException(status_code=500, detail=f"예시 release를 읽지 못했습니다: {e}")
        p = store.create_project(ex["name"], ex["desc"], ex["scoring"], ex["rule"], {"name": ex["validation"]["name"], "cases": cases})
        for r, res in reads:
            store.add_release(p["id"], r["name"], HOW_LABEL["builtin"], str(folder / r["dir"]), res["code_hash"], res["graph"])
        return store.project(p["id"])

    @app.post("/api/projects/{pid}/validation-data")
    def add_validation_data(pid: str, f: FileUpload):
        if not store.project(pid):
            raise HTTPException(status_code=404, detail="Project가 없습니다")
        if store.has_validation_data(pid):
            raise HTTPException(status_code=409, detail="검증데이터는 Project에 하나이며 바꾸거나 지울 수 없습니다")  # UA-15
        return store.add_validation_data(pid, f.name, parse_validation(f))

    class PageFiles(StaticFiles):
        """D-457 B': the page's own files say no-cache — a reload after a fix reads the new app.js (an ETag match still answers 304)."""
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount("/", PageFiles(directory=WEB_DIR, html=True), name="web")
    return app
