from fastapi import APIRouter

from corp_os import __version__
from corp_os.config import get_settings
from corp_os.schemas import HealthOut

router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    from corp_os.services.redis_client import redis_backend, redis_ping

    settings = get_settings()
    info = redis_ping()
    redis_status = redis_backend()
    if info.get("ok"):
        redis_status = "ok"
    elif redis_status == "uninitialized":
        redis_status = str(info.get("backend") or "unknown")
    elif not info.get("ok"):
        redis_status = f"down:{info.get('backend')}"
    return HealthOut(
        status="ok",
        app=settings.app_name,
        version=__version__,
        redis=redis_status,
    )
