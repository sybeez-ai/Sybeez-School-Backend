FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic-settings httpx
COPY apps/ai /app
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
