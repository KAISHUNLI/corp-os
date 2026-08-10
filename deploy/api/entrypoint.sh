#!/bin/sh
set -e

echo "[entrypoint] waiting for database..."
python - <<'PY'
import os, sys, time
from urllib.parse import urlparse

url = os.environ.get("CORP_OS_DATABASE_URL", "")
# postgresql+psycopg://user:pass@host:port/db
u = urlparse(url.replace("postgresql+psycopg://", "postgresql://", 1))
host = u.hostname or "postgres"
port = u.port or 5432

import socket

for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] database reachable at {host}:{port}")
            sys.exit(0)
    except OSError:
        time.sleep(2)
print("[entrypoint] database not ready", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] waiting for milvus (optional)..."
python - <<'PY'
import os, sys, time
from urllib.parse import urlparse

uri = os.environ.get("CORP_OS_MILVUS_URI", "http://milvus:19530")
u = urlparse(uri)
host = u.hostname or "milvus"
port = u.port or 19530

import socket

for i in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] milvus reachable at {host}:{port}")
            sys.exit(0)
    except OSError:
        time.sleep(2)
print("[entrypoint] milvus not ready yet; api will retry at runtime", file=sys.stderr)
sys.exit(0)
PY

mkdir -p "${CORP_OS_UPLOAD_DIR:-/data/uploads}" "${HF_HOME:-/data/hf-cache}"

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting api"
exec uvicorn corp_os.app:app --host 0.0.0.0 --port 8001 --app-dir src
