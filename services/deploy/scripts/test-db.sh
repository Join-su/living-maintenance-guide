#!/bin/sh
# logDB 시험: 공유 기록기 → PostgreSQL trace(llmops-lite-v2 db/compose.yaml의 로컬 DB, 127.0.0.1:55432).
# 시험마다 새 DB를 만들고 지운다(개발 DB llmopsagent는 건드리지 않음). DB가 없으면 실패한다.
# --tb=short, 지역 변수 출력 없음: 실패해도 DB 비밀번호가 출력되지 않는다.
DEPLOY_PG=1 exec "$(dirname "$0")/py" -m pytest -p no:cacheprovider -q --tb=short -m pg "$@"
