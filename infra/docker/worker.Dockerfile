FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir redis pydantic-settings boto3
COPY apps/worker /app
CMD ["python", "-m", "src.main"]
