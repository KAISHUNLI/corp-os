FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY src ./src
COPY deploy/api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=src
ENV HF_HOME=/data/hf-cache

EXPOSE 8001

HEALTHCHECK --interval=20s --timeout=5s --retries=10 --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/v1/health')"

ENTRYPOINT ["/entrypoint.sh"]
