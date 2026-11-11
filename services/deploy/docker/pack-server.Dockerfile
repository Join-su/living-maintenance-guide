# pack-server image = Pack deployment server (port 8796) + its watcher, in one container (x86 and arm). Build context = the repository root:
#   docker compose build pack-server
# Why one container: the watcher finds the server by its listening port and process (lsof · ps), stops it and starts it again as
# its own child whenever a version is deployed, rolled back or a graph is applied (scripts/autodeploy.py · graph_swap.py · kg_apply.py).
# The code is the copy under services/deploy; packrt and tracewriter come from vendor/agent-pack-runtime (v0.15.0). The graph gate
# and the extractor are the AgentOps copy (services/agentops/llmops). Nothing is cloned and no key is used while building.
ARG BASE_IMAGE=python:3.14.2-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.17
FROM ${UV_IMAGE} AS uv

FROM ${BASE_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY services/deploy/docker/requirements.txt /tmp/req.txt
COPY vendor/agent-pack-runtime /src/agent-pack-runtime
# One venv for everything the container runs: the server, the watcher, the Pack apply tool (it runs the Pack's unit tests),
# the graph loader (neo4j driver) and the graph gate. Pinned names only (no resolving) · wheels only · then the two vendored packages.
RUN uv venv /opt/venv --python /usr/local/bin/python3.14 \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache --no-deps --only-binary :all: -r /tmp/req.txt \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache --no-deps /src/agent-pack-runtime/packrt /src/agent-pack-runtime/tracewriter \
 && VIRTUAL_ENV=/opt/venv uv pip check

FROM ${BASE_IMAGE}
# git: Pack tags · commits (apply tool · graph apply) · lsof · procps: the watcher finds and restarts the server
# The Pack repository lives in a volume another container made, so git must not refuse it for its owner; commits need a name.
RUN apt-get update && apt-get install -y --no-install-recommends git lsof procps curl && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*' \
 && git config --system user.name pack-server \
 && git config --system user.email pack-server@localhost \
 && mkdir -p /var/lib/deploy
COPY --from=build /opt/venv /opt/venv
COPY services/deploy/deploy_server /app/deploy_server
COPY services/deploy/scripts /app/scripts
COPY services/deploy/pinned.json services/deploy/pyproject.toml /app/
COPY services/deploy/docker/team.yaml.tmpl services/deploy/docker/pack-apply.py services/deploy/docker/pack-switch.sh /app/docker/
COPY services/agentops/llmops /opt/llmops-gate/llmops
# The frozen scripts/serve.sh listens on 127.0.0.1 only; in the image it is replaced by docker/serve.sh (same, but the address comes
# from DEPLOY_HOST — 0.0.0.0 here, so the app and AgentOps reach pack-server:8796). The watcher starts the server with this file too.
COPY services/deploy/docker/serve.sh /app/scripts/serve.sh
COPY services/deploy/docker/pack-server-start.sh /usr/local/bin/pack-server-start
RUN chmod 0755 /usr/local/bin/pack-server-start /app/docker/pack-switch.sh /app/scripts/py /app/scripts/serve.sh /app/scripts/r12-update
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 DEPLOY_VENV=/opt/venv ORT_DISABLE_TELEMETRY=1
WORKDIR /app
EXPOSE 8796
CMD ["pack-server-start"]
