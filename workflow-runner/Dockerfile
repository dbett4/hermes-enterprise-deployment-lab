FROM docker.io/library/python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY workflow_runner ./workflow_runner

ENTRYPOINT ["python", "-m", "workflow_runner"]
