from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from corp_os import __version__
from corp_os.api import api_router
from corp_os.config import get_settings
from corp_os.db import SessionLocal, init_db
from corp_os.services.auth import ensure_demo_passwords, ensure_password_column
from corp_os.services.seed import seed_if_empty


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        ensure_password_column(db)
        seed_if_empty(db)
        ensure_demo_passwords(db)
    finally:
        db.close()
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
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
