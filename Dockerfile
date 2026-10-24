FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p data artifacts && useradd -m pilot && chown -R pilot:pilot /app
USER pilot
EXPOSE 8000
CMD ["sh", "-c", "uvicorn quantpilot.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]

