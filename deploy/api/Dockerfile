FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10 \
    UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    UV_HTTP_TIMEOUT=180

COPY requirements.txt .

# 1) 用国内源装 uv（比裸 pip 快很多）
# 2) 先装 CPU 版 torch，避免默认拉到超大 CUDA 包
# 3) 再装业务依赖（排除 pytest）
RUN set -eux; \
  grep -vE '^pytest([>=<]|$)' requirements.txt > /tmp/requirements-prod.txt; \
  pip install -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com uv; \
  uv pip install --system --index-url https://download.pytorch.org/whl/cpu torch; \
  uv pip install --system -r /tmp/requirements-prod.txt

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
