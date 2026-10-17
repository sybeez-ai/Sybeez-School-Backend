FROM python:3.12-slim

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

COPY apps/api/pyproject.toml /tmp/pyproject.toml
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" "sqlalchemy[asyncio]>=2.0.36" \
    "asyncpg>=0.30.0" "psycopg2-binary>=2.9.10" "alembic>=1.14.0" \
    "pydantic>=2.10.0" "pydantic-settings>=2.6.0" "python-jose[cryptography]>=3.3.0" \
    "passlib[argon2]>=1.7.4" "argon2-cffi>=23.1.0" "redis>=5.2.0" "httpx>=0.28.0" \
    "email-validator>=2.2.0" "python-multipart>=0.0.17" "pgvector>=0.3.6" \
    "boto3>=1.35.0"

COPY apps/api /app
RUN chown -R app:app /app
USER app

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
