"""서버 데이터 폴더의 파일 기록: 반복 요청 키, 실행 요약, 보고서(API 계약 3·5·6절).

정본 실행 기록은 trace입니다. 배포단 계정은 trace를 읽지 못하므로(공통 DB 설계 5절) 응답에 필요한 요약만 여기에 둡니다.
운영 1단계에서 뺀 배포단 로컬 SQLite(outbox·순번·checkpoint)와는 다릅니다. 여기에는 API 응답과 보고서 파일만 있습니다."""
import hashlib
import json
import os
import threading
from pathlib import Path

from .errors import ApiError
from .ids import new_report_id, now_iso


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


MEDIA = {"md": "text/markdown; charset=utf-8", "json": "application/json", "csv": "text/csv; charset=utf-8",
         "ddl": "text/plain; charset=utf-8", "mermaid": "text/plain; charset=utf-8"}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def idem_scope(deployment_id, pack_revision, boot_id):
    """실행 키 저장 범위(D-296): 배포 id + Pack revision + 서버 시작. 같은 서버 안 재시도는 같은 범위(재생),
    Pack version이 바뀌거나 서버를 다시 띄우면 새 범위(같은 키도 새 실행) — 옛 Pack 결과 재생을 막음."""
    return canonical({"deployment_id": deployment_id, "pack_revision": pack_revision, "boot": boot_id})


class IdempotencyStore:
    """root/<sha256(범위 + key)>.json = {key_sha256, request_sha256, run_id, state: running|done, response: {status, body}}.
    키 자체는 저장하지 않고 hash만 둡니다(키에 무엇이 들어올지 모름). scope가 있으면 파일 이름 hash에 범위를 섞음(D-296, 실행 키만 —
    피드백 키는 범위 없음: 서버를 다시 띄운 뒤 같은 키로 다시 쓰는 길이 약속, 지시 041)."""

    def __init__(self, root, scope=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.scope = scope

    def _path(self, key):
        raw = key if self.scope is None else f"{self.scope}\n{key}"
        return self.root / f"{sha256_hex(raw.encode('utf-8'))}.json"

    def lookup(self, key, request_sha):
        """기록이 없으면 None. 있으면 같은 본문이고 끝난 기록만 돌려주고, 나머지는 ApiError."""
        p = self._path(key)
        if not p.exists():
            return None
        rec = _read_json(p)
        if rec["request_sha256"] != request_sha:
            raise ApiError(422, "idempotency_key_reused", "같은 Idempotency-Key가 다른 본문으로 왔습니다. 새 요청에는 새 키를 쓰세요",
                           where="header:Idempotency-Key", run_id=rec.get("run_id"))
        if rec["state"] not in ("done", "retry"):   # retry = 피드백 기록이 닿지 못한 키(같은 키로 다시 씀, 지시 041)
            raise ApiError(409, "idempotency_in_progress", "같은 Idempotency-Key의 앞 요청이 아직 실행 중입니다. 잠시 뒤 같은 키로 다시 보내세요",
                           where="header:Idempotency-Key", retryable=True, run_id=rec.get("run_id"))
        return rec

    def claim(self, key, request_sha, run_id, extra=None):
        """키를 잡는다(파일을 O_EXCL로 만듦). 이미 있으면 False(동시에 온 같은 키). extra: 다시 쓸 때 필요한 칸(피드백 id · 시각)."""
        p = self._path(key)
        rec = {"key_sha256": p.stem, "request_sha256": request_sha, "run_id": run_id, "state": "running", "claimed_at": now_iso(), "response": None,
               **(extra or {})}
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, indent=1))
        return True

    def complete(self, key, status, body):
        p = self._path(key)
        rec = _read_json(p)
        rec.update(state="done", completed_at=now_iso(), response={"status": status, "body": body})
        _write_json(p, rec)

    def retry(self, key):
        """기록이 닿지 못함(trace_unavailable): 응답을 저장하지 않고 같은 키로 다시 받게 둔다(피드백 키만, 지시 041)."""
        p = self._path(key)
        rec = _read_json(p)
        rec.update(state="retry", retry_at=now_iso())
        _write_json(p, rec)

    def reclaim(self, key):
        """retry 키를 다시 잡는다(같은 칸 그대로 running으로)."""
        p = self._path(key)
        rec = _read_json(p)
        rec.update(state="running", claimed_at=now_iso())
        _write_json(p, rec)

    def reopen(self, p):
        """다시 켤 때 running으로 남은 피드백 키 → retry(같은 키로 오면 같은 id · 시각으로 다시 씀 — 기록기가 같은 내용은 duplicate)."""
        rec = _read_json(p)
        rec.update(state="retry", retry_at=now_iso())
        _write_json(p, rec)

    def running(self):
        for p in sorted(self.root.glob("*.json")):
            rec = _read_json(p)
            if rec["state"] == "running":
                yield p, rec

    def close_interrupted(self, p, status, body):
        rec = _read_json(p)
        rec.update(state="done", completed_at=now_iso(), response={"status": status, "body": body}, interrupted=True)
        _write_json(p, rec)


class RunStore:
    """root/<run_id>.json = GET /runs/{run_id} 응답 본문."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, record):
        _write_json(self.root / f"{record['run_id']}.json", record)

    def get(self, run_id):
        if not run_id.startswith("run_") or not run_id[4:].isalnum():
            return None
        p = self.root / f"{run_id}.json"
        return _read_json(p) if p.exists() else None

    def count(self):
        return len(list(self.root.glob("run_*.json")))

    def list(self, limit, before=None):
        """최신 순(run_id = ULID 내림차순) 한 쪽: (실행 요약에서 result를 뺀 항목들, 다음 쪽 before 또는 None). 이름만 정렬하고 그 쪽 파일만 읽는다."""
        names = sorted((p.name[:-len(".json")] for p in self.root.glob("run_*.json")), reverse=True)
        if before is not None:
            names = [n for n in names if n < before]
        page = names[:limit]
        items = [{k: v for k, v in _read_json(self.root / f"{n}.json").items() if k != "result"} for n in page]
        return items, (page[-1] if len(names) > limit else None)


class ReportStore:
    """data_dir/reports/<report_id>/report.<format> + meta.json. location은 data_dir 기준 상대 경로(trace artifact_written.location)."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        (self.data_dir / "reports").mkdir(parents=True, exist_ok=True)

    def write(self, run_id, node_path, fmt, text, review=None, provider=None):
        rid = new_report_id()
        rel = Path("reports") / rid / f"report.{fmt}"
        data = text.encode("utf-8")
        f = self.data_dir / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(data)
        os.chmod(f, 0o444)   # 보고서는 바뀌지 않는다
        meta = {"report_id": rid, "run_id": run_id, "node_path": node_path, "format": fmt, "media_type": MEDIA.get(fmt, "application/octet-stream"),
                "sha256": sha256_hex(data), "size": len(data), "location": rel.as_posix(), "created_at": now_iso(), "review": review,
                "provider": provider}
        _write_json(f.parent / "meta.json", meta)
        return meta

    def get(self, report_id):
        if not report_id.startswith("rpt_") or not report_id[4:].isalnum():
            return None
        p = self.data_dir / "reports" / report_id / "meta.json"
        return _read_json(p) if p.exists() else None


def request_sha256(value):
    return sha256_hex(canonical(value).encode("utf-8"))
