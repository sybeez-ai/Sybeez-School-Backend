FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic-settings httpx
COPY . .
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
