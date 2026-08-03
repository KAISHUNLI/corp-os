from fastapi import APIRouter

from corp_os import __version__
from corp_os.config import get_settings
from corp_os.schemas import HealthOut

router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(status="ok", app=settings.app_name, version=__version__)
