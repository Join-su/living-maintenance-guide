"""[dev-2] 시연 8796 실제 AI 셈(상한 $0.05 · D-576) — 기준 시각 뒤 생긴 trace 원문 파일의 gpt-4.1-mini 사용(tokens)을 더함. 값 · 글 출력 0(수만).
사용: python3 demo/dev2/llm_count.py <기준 시각 ISO> [trace 원문 폴더 · 기본 = 시연 설정 payload_root]"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else (Path(__file__).resolve().parents[2] / "config").joinpath("../../../llmopsagent-deploy/var/v1-pg-local/trace-payloads").resolve()
SINCE = datetime.fromisoformat(sys.argv[1]).timestamp()
IN, OUT = 0.40 / 1e6, 1.60 / 1e6          # gpt-4.1-mini 1M token 값(USD)


def walk(o, acc):
    if isinstance(o, dict):
        u = o.get("usage")
        if isinstance(u, dict) and "prompt_tokens" in u and "completion_tokens" in u and "gpt-4.1-mini" in json.dumps(o.get("model", "")):
            acc[0] += 1
            acc[1] += u["prompt_tokens"]
            acc[2] += u["completion_tokens"]
        for v in o.values():
            walk(v, acc)
    elif isinstance(o, list):
        for v in o:
            walk(v, acc)


def main():
    acc, files = [0, 0, 0], 0
    for d, _, fs in os.walk(ROOT):
        for f in fs:
            p = os.path.join(d, f)
            if os.path.getmtime(p) < SINCE:
                continue
            files += 1
            try:
                walk(json.load(open(p, encoding="utf-8")), acc)
            except (OSError, ValueError):
                pass
    print(json.dumps({"files": files, "calls": acc[0], "tokens_in": acc[1], "tokens_out": acc[2], "usd": round(acc[1] * IN + acc[2] * OUT, 6), "cap": 0.05}))


if __name__ == "__main__":
    main()
