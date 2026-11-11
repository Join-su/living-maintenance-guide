# AgentOps image for this repository (local compose · x86 and arm). Build context = the repository root:
#   docker compose build agentops
# The code is the copy under services/agentops; packrt and tracewriter come from vendor/agent-pack-runtime (the same version the
# lock file names). Nothing is cloned and no key is used while building. The server starts with an empty data folder.
ARG BASE_IMAGE=python:3.14.2-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.17
FROM ${UV_IMAGE} AS uv

FROM ${BASE_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY services/agentops /src/llmops
COPY vendor/agent-pack-runtime /src/agent-pack-runtime
# Locked third-party packages (uv.lock, offline export), then the two vendored packages without resolving again
RUN cd /src/llmops \
 && UV_OFFLINE=1 uv export --frozen --no-dev --no-hashes --no-emit-project --no-emit-package packrt --no-emit-package tracewriter -o /tmp/req.txt \
 && uv venv /opt/venv --python /usr/local/bin/python3.14 \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r /tmp/req.txt \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache --no-deps /src/agent-pack-runtime/packrt /src/agent-pack-runtime/tracewriter
# The Pack's own Python environment (execution-config.yaml: /opt/pack-venv) — the same pinned set as pack-server (onnxruntime …)
COPY services/deploy/docker/requirements.txt /tmp/pack-req.txt
RUN uv venv /opt/pack-venv --python /usr/local/bin/python3.14 \
 && VIRTUAL_ENV=/opt/pack-venv uv pip install --no-cache --no-deps --only-binary :all: -r /tmp/pack-req.txt \
 && VIRTUAL_ENV=/opt/pack-venv uv pip install --no-cache --no-deps /src/agent-pack-runtime/packrt /src/agent-pack-runtime/tracewriter
# The packrt folder a Pack process runs (<code>/.runtime) is made once here, so the code stays read-only at run time
RUN cd /src/llmops && /opt/venv/bin/python -I -c "import sys; sys.path.insert(0, '.'); from llmops import packs; print(packs.packrt_root())"

FROM ${BASE_IMAGE}
# git: graph versions are read from the Pack repository's tags · setpriv (util-linux): Pack processes run as the evaluation account
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10003 --no-create-home --home /nonexistent --shell /usr/sbin/nologin packeval \
 && test -x /usr/bin/setpriv \
 && mkdir -p /var/lib/llmops && chmod 755 /var/lib/llmops \
 && git config --system --add safe.directory '*'
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/pack-venv /opt/pack-venv
COPY --from=build /src/llmops/llmops /src/llmops/llmops
COPY --from=build /src/llmops/web /src/llmops/web
COPY --from=build /src/llmops/examples /src/llmops/examples
COPY --from=build /src/llmops/db /src/llmops/db
COPY --from=build /src/llmops/.runtime /src/llmops/.runtime
COPY --from=build /src/llmops/pyproject.toml /src/llmops/uv.lock /src/llmops/
COPY services/agentops/start.sh /usr/local/bin/agentops-start
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 LLMOPS_HOST=0.0.0.0 LLMOPS_PORT=18868 LLMOPS_DATA_DIR=/var/lib/llmops
WORKDIR /src/llmops
EXPOSE 18868
CMD ["agentops-start"]
