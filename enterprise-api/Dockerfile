FROM docker.io/library/python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN install -d -o 65534 -g 65534 -m 0750 /var/lib/enterprise-api/store

EXPOSE 8080

USER 65534:65534

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
