from contextlib import asynccontextmanager
import logging
import os
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from corp_os import __version__
from corp_os.api import api_router
from corp_os.config import get_settings
from corp_os.db import SessionLocal, init_db
from corp_os.services.auth import ensure_demo_passwords, ensure_password_column
from corp_os.services.security_bootstrap import (
    ensure_demo_erp_bindings,
    ensure_erp_username_column,
    ensure_role_permissions,
    validate_security_settings,
)
from corp_os.services.seed import seed_if_empty

logger = logging.getLogger(__name__)


def _apply_hf_endpoint() -> None:
    settings = get_settings()
    endpoint = (settings.hf_endpoint or "").strip()
    if endpoint:
        os.environ.setdefault("HF_ENDPOINT", endpoint)


def _warmup_rag_stack() -> None:
    """Load embedder / Milvus and index seed docs in background (do not block health)."""
    try:
        from corp_os.rag.embeddings import get_embedder
        from corp_os.rag.store import reindex_active_documents
        from corp_os.config import get_settings

        settings = get_settings()
        embedder = get_embedder()
        _ = embedder.embed_query("warmup")
        if (settings.vector_store or "").lower() == "milvus":
            from corp_os.rag import milvus_store

            milvus_store.ensure_collection(dim=len(_))
        db = SessionLocal()
        try:
            stats = reindex_active_documents(db)
            logger.info("RAG reindex finished: %s", stats)
        finally:
            db.close()
        logger.info("RAG warmup finished (%s / %s)", settings.embedding_provider, settings.vector_store)
    except Exception:  # noqa: BLE001
        logger.exception("RAG warmup failed (chat will still work, first RAG may be slow)")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _apply_hf_endpoint()
    validate_security_settings()
    init_db()
    db = SessionLocal()
    try:
        ensure_password_column(db)
        ensure_erp_username_column(db)
        seed_if_empty(db)
        ensure_demo_passwords(db)
        ensure_role_permissions(db)
        ensure_demo_erp_bindings(db)
    finally:
        db.close()
    threading.Thread(target=_warmup_rag_stack, name="rag-warmup", daemon=True).start()
    try:
        from corp_os.services.redis_client import redis_ping

        info = redis_ping()
        if info.get("ok"):
            logger.info("Redis ready")
        else:
            logger.warning("Redis not ready: %s", info)
    except Exception:  # noqa: BLE001
        logger.exception("Redis ping failed")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="企业智能体：账号密码登录 + 对话上传 + 按权限 RAG + 重要文件审批",
        lifespan=lifespan,
    )
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
