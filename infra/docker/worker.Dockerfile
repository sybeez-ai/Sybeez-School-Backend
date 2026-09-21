FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir redis pydantic-settings
COPY apps/worker /app
CMD ["python", "-m", "src.main"]
