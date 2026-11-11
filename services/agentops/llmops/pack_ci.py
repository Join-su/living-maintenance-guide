"""pack_ci (EV-VPS-01 CHG-V07, key ①): on the VPS, LLMOps asks the Pack repository's runner to take a deploy-approved change
(GitHub workflow_dispatch) and brings the runner's result back as the Pack status file the approval window already reads.

On only when LLMOPS_PACK_CI_REPO, LLMOPS_PACK_CI_TOKEN_FILE and LLMOPS_PACK_CI_API are all set; otherwise nothing here runs and the
deploy approval writes its request file only, as before (PRES-V07). There is no default address in the code (static_net_scan S6):
the VPS compose names the GitHub API. The token is read from its file for the request header only — never printed, stored or passed
to loaded code (guard drops the file's variable name; every value read joins the masked values). Redirects are never followed with
the token: API calls do not follow them at all, and the artifact download (GitHub answers it with a redirect to its blob host) follows
one hop of the same scheme without any header (review VS-4 H-1).

- dispatch(change_id, request text): POST …/actions/workflows/<workflow>/dispatches with inputs change_id · request · dry_run
  ("true" unless LLMOPS_PACK_CI_DRY_RUN=false). The outcome goes to <data>/pack-ci/<change_id>.json: requested (2xx), refused (4xx,
  no answer) or unknown (5xx, time-out, a dropped connection — GitHub may have taken it). It never raises and never touches the
  decision or the request file (PRES-V08).
- refresh(change_id), from the approval window's read, at most once per 30 seconds per change: the run named 'deploy <change_id>'
  (the workflow's run-name) → running: status 'requested' · '러너 실행 중'; finished: the artifact 'ci-status-<change_id>'
  (task10-pack/ci-deploy-status@1) → the Pack status folder as <change_id>.json, the shape deploying.read_status reads. No run yet:
  a refused request is sent again after 30 seconds, an unknown one only after 60 seconds without a run (so a run GitHub lists late
  is not started twice), at most three requests in all. It never raises.
Names (V-22): change ids look like CHG-<pack>-<version>-<8 hex> (no ':'), run 'deploy <change_id>', artifact 'ci-status-<change_id>'."""
import http.client
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ENV = {"repo": "LLMOPS_PACK_CI_REPO", "workflow": "LLMOPS_PACK_CI_WORKFLOW", "ref": "LLMOPS_PACK_CI_REF", "token_file": "LLMOPS_PACK_CI_TOKEN_FILE",
       "dry_run": "LLMOPS_PACK_CI_DRY_RUN", "api": "LLMOPS_PACK_CI_API"}
CHANGE_RE = re.compile(r"^CHG-[A-Za-z0-9._-]{1,120}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
STATUS_FORMAT = "task10-pack/ci-deploy-status@1"
STAGES = ("requested", "applied", "tagged", "failed")
TIMEOUT_S, MIN_GAP_S, UNKNOWN_GAP_S, MAX_TRIES = 10, 30, 60, 3


def run_name(change_id):
    return f"deploy {change_id}"


def artifact_name(change_id):
    return f"ci-status-{change_id}"


@dataclass(frozen=True)
class Config:
    repo: str
    workflow: str
    ref: str
    token_file: Path
    dry_run: bool
    api: str


def config(env):
    """The pack_ci setting, or None (off) when one of the three required names is missing. A malformed value refuses to start."""
    if not all(env.get(ENV[k]) for k in ("repo", "token_file", "api")):
        return None
    repo, api = env[ENV["repo"]], env[ENV["api"]].rstrip("/")
    if not REPO_RE.match(repo):
        raise SystemExit(f"{ENV['repo']}는 '<owner>/<저장소>' 모양이어야 합니다")
    if not re.match(r"^https?://[^/\s]+$", api):
        raise SystemExit(f"{ENV['api']}는 'https://<호스트>' 모양이어야 합니다")
    dry = (env.get(ENV["dry_run"]) or "true").strip().lower()
    if dry not in ("true", "false"):
        raise SystemExit(f"{ENV['dry_run']}는 true 또는 false입니다")
    return Config(repo=repo, workflow=env.get(ENV["workflow"]) or "deploy-approved-change.yml", ref=env.get(ENV["ref"]) or "dev",
                  token_file=Path(env[ENV["token_file"]]), dry_run=dry != "false", api=api)


class CIError(Exception):
    def __init__(self, reason, taken_unknown=False):
        super().__init__(reason)
        self.reason, self.taken_unknown = reason, taken_unknown


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None   # a 30x comes back as an HTTPError with its Location; nothing is sent anywhere else


_OPENER = urllib.request.build_opener(_NoRedirect)


def _atomic_write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def to_status(doc, change_id):
    """task10-pack/ci-deploy-status@1 → the status-file shape (deploying.FIELDS). A finished run without a stage is a failure, so the
    window never waits on a run that is over (critic E3-A2) — except a rehearsal, which leaves no stage when it passes (the workflow
    stops before taking the change): that is a passed dry run at 'requested' (review VS-4 M-4)."""
    if not isinstance(doc, dict) or doc.get("format") != STATUS_FORMAT or doc.get("change_id") != change_id:
        return {"id": change_id, "stage": "failed", "detail": "러너 결과 파일의 형식이 다릅니다"}
    out = {"id": change_id, "updated_at": doc.get("updated_at")}
    if doc.get("state") == "failed":
        out.update(stage="failed", detail=(doc.get("reason") or "러너 실패")[:300])
    elif doc.get("state") == "succeeded" and doc.get("pack_stage") in STAGES:
        out.update(stage=doc["pack_stage"], revision=doc.get("revision"), commit=doc.get("commit"), tag=doc.get("tag"))
    elif doc.get("state") == "succeeded" and doc.get("dry_run") is True:
        out.update(stage="requested", detail="시험 실행 통과")
    elif doc.get("state") == "succeeded":
        out.update(stage="failed", detail="러너 결과에 단계가 없습니다")
    else:
        out.update(stage="requested", detail="러너 실행 중")
    if doc.get("dry_run") is True:
        out["dry_run"] = True
    return out


class PackCI:
    def __init__(self, cfg, data_dir, status_dir=None, clock=time.time, on_secret=None):
        self.cfg, self.clock = cfg, clock
        self.records = Path(data_dir) / "pack-ci"
        self.status_dir = Path(status_dir) if status_dir else Path(data_dir) / "pack-status"
        self.on_secret = on_secret or (lambda v: None)
        self.lock = threading.Lock()
        self.last = {}   # change id → time of the last refresh

    # ---------------------------------------------------------------- HTTP
    def token(self):
        try:
            t = self.cfg.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise CIError("토큰 파일을 읽을 수 없습니다") from None
        if not t or "\n" in t:
            raise CIError("토큰 파일이 비었거나 모양이 다릅니다")
        self.on_secret(t)   # every value read is masked from now on (a replaced token too)
        return t

    def _send(self, req):
        """(status, body, Location) — a 30x is returned, never followed. Raises CIError for no or a broken answer."""
        try:
            with _OPENER.open(req, timeout=TIMEOUT_S) as r:
                return r.status, r.read(), None
        except urllib.error.HTTPError as e:
            return e.code, b"", e.headers.get("Location") if e.headers else None
        except (TimeoutError, urllib.error.URLError, http.client.HTTPException, ConnectionError, OSError) as e:
            reason = getattr(e, "reason", None)
            if isinstance(e, (TimeoutError, http.client.HTTPException, ConnectionResetError)) or isinstance(reason, TimeoutError):
                raise CIError("응답이 끊김 또는 시간 초과", taken_unknown=True) from None   # the request may have arrived
            raise CIError("GitHub에 연결할 수 없음") from None

    def _call(self, method, path_or_url, body=None):
        url = path_or_url if path_or_url.startswith(self.cfg.api + "/") else self.cfg.api + path_or_url
        if not url.startswith(self.cfg.api + "/"):
            raise CIError("다른 주소로는 요청하지 않습니다")
        req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                                              **({"Content-Type": "application/json"} if body is not None else {})})
        req.add_unredirected_header("Authorization", f"Bearer {self.token()}")   # never forwarded on a redirect (review VS-4 H-1)
        return self._send(req)

    def _json(self, data, what):
        try:
            doc = json.loads(data or b"{}")
        except ValueError:
            raise CIError(f"{what}의 답이 JSON이 아닙니다") from None
        if not isinstance(doc, dict):
            raise CIError(f"{what}의 답 모양이 다릅니다")
        return doc

    # ---------------------------------------------------------------- records
    def record(self, change_id):
        try:
            rec = json.loads((self.records / f"{change_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return rec if isinstance(rec, dict) and isinstance(rec.get("tries", []), list) else None   # a damaged record counts as none (review VS-4 L-3)

    def _note(self, change_id, request_text, result, reason=None):
        rec = self.record(change_id) or {"change_id": change_id, "dry_run": self.cfg.dry_run, "tries": []}
        rec["tries"].append({"at": self.clock(), "result": result, **({"reason": reason} if reason else {})})
        rec["requested"] = rec.get("requested") or result == "requested"
        rec["request"] = request_text
        _atomic_write(self.records / f"{change_id}.json", rec)
        return rec

    # ---------------------------------------------------------------- dispatch
    def dispatch(self, change_id, request_text):
        """{"requested": bool, "reason"?}. Never raises after the change id check (PRES-V08)."""
        if not CHANGE_RE.match(change_id or ""):
            raise ValueError(f"change id 모양이 다릅니다(':' 등 금지): {change_id!r}")
        body = {"ref": self.cfg.ref, "inputs": {"change_id": change_id, "request": request_text, "dry_run": "true" if self.cfg.dry_run else "false"}}
        try:
            status, _, _ = self._call("POST", f"/repos/{self.cfg.repo}/actions/workflows/{self.cfg.workflow}/dispatches", body)
            if 200 <= status < 300:
                result, reason = "requested", None
            elif status >= 500:
                result, reason = "unknown", f"GitHub 오류({status})"
            else:
                result, reason = "refused", f"GitHub가 받지 않음({status})"
        except CIError as e:
            result, reason = ("unknown" if e.taken_unknown else "refused"), e.reason
        except Exception as e:  # noqa: BLE001 - whatever happens, the approval stays as it is; the kind only
            result, reason = "unknown", f"요청 중 오류({type(e).__name__})"
        try:
            self._note(change_id, request_text, result, reason)
        except OSError:
            pass
        return {"requested": True} if result == "requested" else {"requested": False, "reason": reason}

    def failure_note(self, change_id):
        """The step ② text while there is no status file and the request was not taken, or None."""
        rec = self.record(change_id)
        if not rec or rec.get("requested") or not rec.get("tries"):
            return None
        reason = rec["tries"][-1].get("reason") or "알 수 없음"
        tail = "관리자 확인 필요" if len(rec["tries"]) >= MAX_TRIES else "다시 열면 다시 요청"
        return f"실행 요청 실패 — {reason}({tail})"

    # ---------------------------------------------------------------- refresh
    def _find_run(self, change_id):
        status, data, _ = self._call("GET", f"/repos/{self.cfg.repo}/actions/workflows/{self.cfg.workflow}/runs?event=workflow_dispatch&per_page=50")
        if status != 200:
            raise CIError(f"실행 목록을 읽지 못함({status})")
        runs = [r for r in (self._json(data, "실행 목록").get("workflow_runs") or [])
                if isinstance(r, dict) and isinstance(r.get("id"), int) and (r.get("display_title") == run_name(change_id) or r.get("name") == run_name(change_id))]
        return max(runs, key=lambda r: r["id"]) if runs else None

    def _download(self, url):
        """The artifact zip: API answer, or one redirect hop of the same scheme fetched without any header."""
        status, blob, loc = self._call("GET", url)
        if status in (301, 302, 303, 307, 308) and loc:
            if urllib.parse.urlsplit(loc).scheme != urllib.parse.urlsplit(self.cfg.api).scheme:
                raise CIError("결과 파일 주소의 형식이 다릅니다")
            status, blob, _ = self._send(urllib.request.Request(loc, method="GET"))
        if status != 200:
            raise CIError(f"결과 파일을 받지 못함({status})")
        return blob

    def _artifact(self, run, change_id):
        status, data, _ = self._call("GET", f"/repos/{self.cfg.repo}/actions/runs/{run['id']}/artifacts")
        if status != 200:
            raise CIError(f"결과 목록을 읽지 못함({status})")
        arts = [a for a in (self._json(data, "결과 목록").get("artifacts") or [])
                if isinstance(a, dict) and a.get("name") == artifact_name(change_id) and not a.get("expired") and isinstance(a.get("archive_download_url"), str)]
        if not arts:
            return {"id": change_id, "stage": "failed", "detail": "러너 결과 파일이 없습니다"}
        blob = self._download(arts[0]["archive_download_url"])
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                doc = json.loads(z.read("ci-status.json"))
        except (zipfile.BadZipFile, KeyError, ValueError):
            return {"id": change_id, "stage": "failed", "detail": "러너 결과 파일을 읽을 수 없습니다"}
        return to_status(doc, change_id)

    def refresh(self, change_id):
        """Bring the runner's state into the Pack status folder. Never raises (a failed look tries again on a later read)."""
        try:
            self._refresh(change_id)
        except Exception:  # noqa: BLE001 - the approval window must always answer
            return

    def _refresh(self, change_id):
        with self.lock:
            now = self.clock()
            if now - self.last.get(change_id, -MIN_GAP_S) < MIN_GAP_S:
                return
            self.last[change_id] = now
        rec = self.record(change_id)
        if rec is None or rec.get("done"):
            return   # never asked for here (e.g. before pack_ci was on), or its finished run was already read (a passed dry run too)
        cur = self.status_dir / f"{change_id}.json"
        try:
            old = json.loads(cur.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict) and old.get("stage") in ("tagged", "failed"):
            return
        try:
            run = self._find_run(change_id)
        except CIError:
            return
        if run is None:
            tries = rec.get("tries") or [{}]
            last = tries[-1]
            gap = UNKNOWN_GAP_S if last.get("result") == "unknown" else MIN_GAP_S
            if not rec.get("requested") and last.get("result") in ("refused", "unknown") and len(tries) < MAX_TRIES \
                    and now - (last.get("at") or 0) >= gap:
                self.dispatch(change_id, rec.get("request") or "")
            return
        if run.get("status") != "completed":
            doc = {"id": change_id, "stage": "requested", "detail": "러너 실행 중"}
            if rec.get("dry_run"):
                doc["dry_run"] = True
        else:
            try:
                doc = self._artifact(run, change_id)
            except CIError:
                return
        _atomic_write(cur, doc)
        if run.get("status") == "completed":   # a finished run is read once: no more looks, and an expired artifact cannot undo it (review VS-4 M-1)
            rec["done"] = run["id"]
            _atomic_write(self.records / f"{change_id}.json", rec)
