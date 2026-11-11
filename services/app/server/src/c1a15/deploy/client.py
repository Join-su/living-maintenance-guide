"""배포 서버 클라이언트(EV-001 CH-15): 앱 → 배포 서버 `POST /runs` 한 곳.

`C1A15_JUDGE=deploy` 길의 Pack Run(alarm · start · judge)은 모두 여기로 간다.
배포 서버 API 계약 v0.9 10절(앱이 지킬 약속):
- 키: 기본 키 `c1a15-<kind>-<id>-<seq>-<본문 sha256 앞 16>`(같은 판정 요청 = 같은 기본 키),
  시도 키 = 기본 키 + `-r<n>`. n = 그 기본 키로 남은 '저장된 retryable 실패' 감사 수
  — 감사는 요청 transaction과 따로 commit되므로 되돌림 뒤에도 남는다.
- 시간 초과: 첫 Run(alarm · start) 90초 · 다음 Run(judge) 30초.
  연결 실패 · 시간 초과 · 502 · 504 = 같은 키 3번(2 · 5 · 10초).
- 409 idempotency_in_progress = `GET /runs/{run_id}` 2초마다 · 120초까지(run_id가 없으면 같은 키 POST로 다시).
- run_interrupted · trace_unavailable · run_failed(retryable) = 실패가 키에 저장됨 → 감사 뒤 다음 시도 키로 한 번.
- 그 밖(400 · 422 · deployment_not_registered · retryable 아닌 run_failed · internal_error · 표 밖 code)
  = 다시 보내지 않음.
끝내 실패하면 PackUnavailable — 앱은 자기 화면에 503 pack_unavailable(앱 쪽 코드, D-265)을 돌려준다.
inproc로 대신하지 않는다.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

TIMEOUT = {"alarm": 90.0, "start": 90.0, "judge": 30.0, "report": 90.0}  # report = Q-19 답 전 제안값(LLM 다시 쓰기까지)
TIMEOUT["exception_split"] = 90.0  # AI 나눔(약속 v10 2절 · 호출 1번)
TIMEOUT["exception_similar"] = 30.0  # 약속 v10 3절 비슷한 것 찾기(호출 0 · 그래프 파일만)
BACKOFF = (2.0, 5.0, 10.0)  # 같은 키 다시 보내기 간격(3번)
NO_RUN_ID_WAIT = 2.0  # 409인데 run_id 없음 — 2초 뒤 같은 키(3번 안, deploy 답 R6-5)
POLL_EVERY, POLL_LIMIT = 2.0, 120.0
NEW_KEY = {"run_interrupted", "trace_unavailable"}  # 실행은 새 키로(계약 3 · 4절)
STORED = NEW_KEY | {  # 실행을 시작한 뒤의 실패 — 키에 저장돼 같은 키로는 같은 실패가 돌아옴(계약 4절 '키 저장')
    "run_failed",
    "deployment_not_registered",
    "trace_schema",
    "trace_conflict",
    "payload_failed",
    "payload_too_large",
    "artifact_failed",
}
LOCAL_HOSTS = {"127.0.0.1"}  # 맥 host 직접 = 127.0.0.1 포트만(D-261 (4))
# 컨테이너(compose · VPS) = 내부망 이름 하나(C1A15_DEPLOY_HOST, EV-001 CH-38)
# — DNS 이름(점으로 나눈 소문자 · 숫자 · - 조각), IP · localhost 아님(D-333 덧 Q-a)
#   예: deploy-example-v1(VPS 서비스 이름 꼴 — 실제 값은 서버 .env에만) · fake-deploy(compose 시험)
#   · host.docker.internal(맥 compose → 맥 배포 창)
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_NAME = re.compile(rf"{LABEL}(?:\.{LABEL})*")


class PackUnavailable(Exception):
    """배포 서버에서 결과를 받지 못함(재시도 뒤).

    code = 배포 서버 오류 code, 또는 앱이 붙인 timeout · unreachable · poll_timeout · bad_response.
    """

    def __init__(self, code: str, run_id: str | None = None, where: str | None = None, node_code: str | None = None):
        super().__init__(code)
        self.code, self.run_id = code, run_id
        self.where, self.node_code = where, node_code  # run_failed의 node 위치 · 오류(D-275: run.predict_failure)


@dataclass(frozen=True)
class RunResult:
    output: dict
    run_id: str
    deployment_id: str | None
    replayed: bool


@dataclass
class Reply:
    status: int | None  # None = 응답을 받지 못함(transport 참고)
    payload: dict | None
    replayed: bool = False
    transport: str | None = None  # timeout · unreachable · bad_response


class Ledger(Protocol):
    """시도 키 번호를 셀 감사 기록 — 요청 transaction과 따로 commit한다."""

    def retryable_stored(self, base_key: str) -> int: ...

    def record(self, detail: dict) -> None: ...


def check_host_name(name: str) -> str:
    if (
        not HOST_NAME.fullmatch(name)
        or len(name) > 253
        or name in ("localhost", "ip6-localhost", "ip6-loopback")  # 컨테이너 /etc/hosts의 자기 자신(review N-b)
        or name.endswith(".localhost")  # RFC 6761 — 늘 자기 자신
        or _ipv4_like(name)
    ):
        raise ValueError(f"C1A15_DEPLOY_HOST는 내부망 이름(DNS 이름 — 소문자 · 숫자 · - · 점, IP 아님): {name!r}")
    return name


def _ipv4_like(name: str) -> bool:
    """IPv4로 읽히는 이름(0x7f000001 · 0x7f.1 · 127.1 등 inet_aton 꼴).

    getaddrinfo가 IP로 풀기 때문에 이름으로 받지 않음(ES-20 review B1).
    """
    parts = name.split(".")  # inet_aton 꼴: 1 ~ 4 조각, 조각마다 10진 · 0x 16진 · 0 8진(socket 없이 — 외부 호출 검사)
    return 1 <= len(parts) <= 4 and all(re.fullmatch(r"0x[0-9a-f]*|[0-9]+", x) for x in parts)


def is_loopback(url: str) -> bool:
    """주소 호스트가 127.0.0.1인가(컨테이너 안이면 컨테이너 자신 — config.pack_settings가 거절)."""
    return urlsplit(url).hostname in LOCAL_HOSTS


def check_url(base: str, host_name: str | None = None) -> str:
    """배포 서버 주소 한 곳: http://127.0.0.1:<port> 또는 http://<host_name>:<port>(host_name = C1A15_DEPLOY_HOST)."""
    parts = urlsplit(base)
    allowed = LOCAL_HOSTS | ({check_host_name(host_name)} if host_name else set())
    if (
        parts.scheme != "http"
        or parts.hostname not in allowed
        or not parts.port
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            f"배포 서버 주소는 http://127.0.0.1:<port> 또는 http://<C1A15_DEPLOY_HOST>:<port>만 허용: {base!r}"
        )
    return base.rstrip("/")


def body_sha(body: dict) -> str:
    """계약 3절 '같은 본문' = 키 순서를 정렬한 정규화 JSON의 sha256."""
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def base_key(kind: str, ident: str, seq: int, body: dict) -> str:
    key = f"c1a15-{kind}-{ident}-{seq}-{body_sha(body)[:16]}"
    if not key.isascii() or any(ch.isspace() or not ch.isprintable() for ch in key) or len(key) > 250:
        raise ValueError(f"키는 공백 없는 ASCII 255자 안(계약 2절): {key!r}")  # -r<n> 자리를 남김
    return key


def attempt_key(base: str, n: int) -> str:
    return base if n == 0 else f"{base}-r{n}"


def _error(payload: dict | None) -> dict:
    err = (payload or {}).get("error") if isinstance(payload, dict) else None
    return err if isinstance(err, dict) else {}


def _node_code(err: dict) -> str | None:
    return ((err.get("details") or {}).get("node_error") or {}).get("code")


def _retryable(err: dict) -> bool:
    if err.get("code") in NEW_KEY:
        return True
    node = (err.get("details") or {}).get("node_error") or {}
    return err.get("code") == "run_failed" and node.get("retryable") is True  # node 오류 칸만 본다(계약 10절 6항)


class Client:
    def __init__(
        self,
        url: str,
        ledger: Ledger,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeouts: dict[str, float] | None = None,
        poll_every: float = POLL_EVERY,
        poll_limit: float = POLL_LIMIT,
        host_name: str | None = None,
    ):
        if poll_every <= 0 or poll_limit <= 0:
            raise ValueError("poll_every · poll_limit은 0보다 커야 함")
        self.url, self.ledger, self.sleep, self.clock = check_url(url, host_name), ledger, sleep, clock
        self.timeouts = {**TIMEOUT, **(timeouts or {})}
        self.poll_every, self.poll_limit = poll_every, poll_limit

    def _http(
        self,
        method: str,
        path: str,
        timeout: float,
        key: str | None = None,
        body: dict | None = None,
        purpose: str | None = None,
    ) -> Reply:
        headers = {"Content-Type": "application/json"} | ({"Idempotency-Key": key} if key else {})
        if purpose:  # D-476 덧 12: 배포 서버가 trace Run 메타 purpose로 남김 → LLMOps 다시 돌리기 · 증거 세기에서 뺌
            headers["X-Run-Purpose"] = purpose
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                replayed = r.headers.get("Idempotent-Replayed") == "true"
                try:
                    payload = json.loads(r.read() or b"null")
                except TimeoutError:
                    return Reply(None, None, transport="timeout")  # 머리글 뒤 본문을 읽다 시간 초과 = 같은 키 갈래
                except ConnectionError:
                    return Reply(None, None, transport="unreachable")
                except Exception:  # JSON 아님 · 본문이 중간에 잘림 — 받은 응답이 깨짐
                    return Reply(r.status, None, replayed, "bad_response")
                return Reply(r.status, payload if isinstance(payload, dict) else None, replayed)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"null")
            except Exception:  # 오류 본문을 못 읽어도 상태 코드로 가름(JSON 아님 · 끊김)
                payload = None
            return Reply(
                e.code, payload if isinstance(payload, dict) else None, e.headers.get("Idempotent-Replayed") == "true"
            )
        except urllib.error.URLError as e:
            timed_out = isinstance(e.reason, TimeoutError)  # socket.timeout = TimeoutError(3.10+)
            return Reply(None, None, transport="timeout" if timed_out else "unreachable")
        except TimeoutError:
            return Reply(None, None, transport="timeout")
        except ConnectionError:
            return Reply(None, None, transport="unreachable")
        except Exception:  # 응답 상태 줄이 깨짐 등 — 같은 키 갈래로 다시(urllib가 감싸지 않는 예외)
            return Reply(None, None, transport="unreachable")

    def info(self, timeout: float = 2.0) -> str | None:
        """지금 도는 Pack revision(GET /info) — 못 읽으면 None(D-619 사전 점검 · KG 반영 뒤 대조)."""
        r = self._http("GET", "/info", timeout)
        pack = (r.payload or {}).get("pack") if r.status == 200 else None
        return pack.get("pack_revision") if isinstance(pack, dict) else None

    def feedback(self, run_id: str, body: dict, key: str, timeout: float) -> Reply:
        """사람 체크 기록 한 장(EV-006 CH-4 · deploy 202): `POST /runs/{run_id}/feedback` 한 번 — 다시 보내지 않음.

        같은 키 = record_key(같은 본문이면 배포 서버가 같은 응답). 실패는 부르는 쪽이 감사만 남긴다(승인 그대로).
        """
        return self._http("POST", f"/runs/{run_id}/feedback", timeout, key, body)

    def _poll(self, run_id: str, timeout: float) -> Reply | None:
        """409 뒤: 앞 요청이 만든 실행이 끝날 때까지 2초마다 읽음(최대 120초 — GET에 걸린 시간도 셈).

        끝나면 그 결과(성공 = 201 모양, 실패 = 그 실행의 오류 · 404 = run_not_found), 120초 안에 안 끝나면 None.
        """
        deadline = self.clock() + self.poll_limit
        while True:
            left = deadline - self.clock()
            if left <= 0:
                return None
            self.sleep(min(self.poll_every, left))
            left = deadline - self.clock()
            if left <= 0:
                return None
            r = self._http("GET", f"/runs/{run_id}", min(timeout, left))
            run = r.payload or {}
            if r.status == 200 and run.get("status") == "succeeded":
                return Reply(201, run)
            if r.status == 200 and run.get("status") == "failed":
                return Reply(500, {"error": run.get("error") or {"code": "run_failed", "run_id": run_id}})
            if r.status == 404:
                return Reply(404, {"error": {**_error(r.payload), "code": "run_not_found", "run_id": run_id}})

    def run(self, kind: str, ident: str, seq: int, body: dict, purpose: str | None = None) -> RunResult:
        """purpose(예 trial) = 머리글 X-Run-Purpose · 키도 따로(c1a15-judge-trial-…) — 같은 본문의 실제 Run과 나눔."""
        base = base_key(f"{kind}-{purpose}" if purpose else kind, ident, seq, body)
        n = self.ledger.retryable_stored(base)
        key, timeout = attempt_key(base, n), self.timeouts[kind]
        same_key_tries, new_key_used = 0, False

        def fail(code: str, err: dict, stored: bool, retryable: bool) -> None:
            self.ledger.record(
                {
                    "kind": kind,
                    "base_key": base,
                    "key": key,
                    "code": code,
                    "run_id": err.get("run_id"),
                    "stored": stored,
                    "retryable": retryable,
                    "where": err.get("where"),
                    "node_code": _node_code(err),
                }
            )

        while True:
            r = self._http("POST", "/runs", timeout, key, body, **({"purpose": purpose} if purpose else {}))
            err = _error(r.payload)
            if r.status == 409 and err.get("code") == "idempotency_in_progress" and err.get("run_id"):
                polled = self._poll(err["run_id"], timeout)
                if polled is None:  # 120초 안에 안 끝남 — 같은 키로 다시 보내지 않고 끝냄(계약 10절 4항)
                    fail("poll_timeout", err, False, False)
                    raise PackUnavailable("poll_timeout", err["run_id"])
                r, err = polled, _error(polled.payload)
            if r.status in (200, 201) and r.transport is None:
                result = (r.payload or {}).get("result")
                if isinstance(result, dict) and isinstance(result.get("output"), dict) and r.payload.get("run_id"):
                    return RunResult(result["output"], r.payload["run_id"], r.payload.get("deployment_id"), r.replayed)
                fail("bad_response", {"run_id": (r.payload or {}).get("run_id")}, False, False)
                raise PackUnavailable("bad_response", (r.payload or {}).get("run_id"))
            code = err.get("code") or r.transport or f"http_{r.status}"
            in_progress_no_id = r.status == 409 and code == "idempotency_in_progress"  # run_id 없음(지금 서버는 안 냄)
            transient = r.transport in ("timeout", "unreachable") or r.status in (502, 504) or in_progress_no_id
            if transient and same_key_tries < len(BACKOFF):  # 같은 키 · 같은 본문 3번(2 · 5 · 10초)
                self.sleep(NO_RUN_ID_WAIT if in_progress_no_id else BACKOFF[same_key_tries])
                same_key_tries += 1
                continue
            stored = code in STORED or (code == "internal_error" and bool(err.get("run_id")))  # 계약 4절 '실행 뒤면 예'
            retryable = stored and _retryable(err)
            fail(code, err, stored, retryable)
            if retryable and not new_key_used:  # 저장된 retryable 실패 → 다음 시도 키로 한 번
                n, new_key_used, same_key_tries = n + 1, True, 0
                key = attempt_key(base, n)
                continue
            raise PackUnavailable(code, err.get("run_id"), err.get("where"), _node_code(err))
