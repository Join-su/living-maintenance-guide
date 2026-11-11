"""정답 자료 파일 열기 감시(AC-15-C2 실행 부분 · ADV-09).

Python audit hook으로 'open' 사건을 보고 정답 자료 경로면 알린다.

정적 검사(scripts/forbidden_paths_check.py)와 같은 경로 모양을 쓴다.
평가 실행(scripts/eval-run)은 api 컨테이너에서 sitecustomize로 켜고,
시험(server/tests/test_eval.py)은 진단 처리를 이 감시로 감싼다. 제품 코드는 이 모듈을 import하지 않는다.
"""

import re
import sys

PATTERN = re.compile(r"eval/cards|cards\.jsonl|card_inputs|card_udi_map|cause_ratio_table|(^|/)generation/")


def make_hook(on_hit):
    def hook(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes)):
            path = args[0].decode(errors="replace") if isinstance(args[0], bytes) else args[0]
            if PATTERN.search(path):
                on_hit(path)

    return hook


def install_printing() -> None:
    sys.addaudithook(make_hook(lambda p: print(f"FORBIDDEN_OPEN {p}", file=sys.stderr, flush=True)))
