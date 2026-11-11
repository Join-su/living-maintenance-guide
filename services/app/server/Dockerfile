# API 서버 · 적재기 공용 image. 같은 uv.lock으로 설치한다(EP-C1-PY-001).
FROM python:3.12-slim
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.12.17
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src
