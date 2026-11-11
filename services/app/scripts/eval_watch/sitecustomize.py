# 평가 실행(scripts/eval-run) 때만 api 컨테이너 PYTHONPATH에 들어가 정답 자료 파일 열기를 감시한다(AC-15-C2 · ADV-09).
from forbidden_open import install_printing

install_printing()
