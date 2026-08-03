from fastapi import APIRouter

from corp_os.api.routes import auth, chat, governance, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(governance.router, prefix="/governance", tags=["governance"])
