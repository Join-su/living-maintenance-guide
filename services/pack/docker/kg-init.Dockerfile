# kg-init — 한 번 돌고 끝나는 컨테이너: 시드 이력 → 지식 그래프(Neo4j) → Pack 그래프 파일 → Pack git 저장소(볼륨) + 첫 태그.
# 빌드 자리 = 저장소 뿌리(docker compose build kg-init). x86 · arm 둘 다(python:3.12-slim 공식 다중 아키텍처).
# 이미지 안 값(키 · 비밀번호) 0 — NEO4J_AUTH는 compose 환경값으로만 받음.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# Pack(c1a15-pack bcaf137 트리 · packrt 0.15.0 vendor) · 벡터 DB 자리 · 이력 합치기 도구 · 시드
COPY services/pack /opt/pack
COPY services/agent-pack-runtime-ingest /opt/agent-pack-runtime-ingest
COPY services/deploy/scripts/demo_dev2.py /opt/tools/demo_dev2.py
COPY data/seed/kg/history-demo-base.jsonl /opt/seed/history-demo-base.jsonl
COPY services/app/data/history/maintenance_history.jsonl /opt/seed/maintenance_history.jsonl
COPY scripts/kg-init.sh /opt/kg-init.sh
COPY scripts/efficiency-packs.py /opt/efficiency-packs.py

# Pack이 쓰는 판 그대로(c1a15-pack pyproject · packrt pyproject) + 그래프 DB 드라이버(kg-test가 쓰는 판)
RUN pip install --no-cache-dir /opt/pack/vendor/packrt "onnxruntime==1.31.0" "neo4j==6.4.1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PACK_SRC=/opt/pack INGEST_SRC=/opt/agent-pack-runtime-ingest SEED_DIR=/opt/seed TOOLS_DIR=/opt/tools \
    PACK_LIVE=/srv/pack-repo PACKS_DIR=/packs LINES_DIR=/srv/seed-lines \
    KGDB_URI=bolt://neo4j:7687 FIRST_PACK_TAG=first WATCH_BASE_TAG=d001-g1 PACK_BRANCH=kg-apply \
    AGENTOPS_EXEC_CONFIG=/srv/agentops-config/execution-config.yaml

ENTRYPOINT ["bash", "/opt/kg-init.sh"]
