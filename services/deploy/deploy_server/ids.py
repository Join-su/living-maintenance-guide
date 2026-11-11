"""서버가 만드는 식별자: report_id = rpt_ + ULID, 시각. run_id·trace_id(traceparent 이어 쓰기)는 공유 기록기 tracewriter의 것을 쓴다."""
import os
import time
from datetime import datetime, timezone

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32


def ulid(now_ms=None):
    t = int(time.time() * 1000) if now_ms is None else int(now_ms)
    n = (t << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_B32[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def new_report_id():
    return "rpt_" + ulid()


def new_feedback_id():
    """사람 피드백 id(지시 041): fb_ + ULID — tracewriter new_feedback_id와 같은 모양. 같은 키 재요청은 처음 만든 id를 다시 씀."""
    return "fb_" + ulid()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
