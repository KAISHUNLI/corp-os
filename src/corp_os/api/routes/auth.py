from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.db import get_db
from corp_os.models.iam import User
from corp_os.schemas import LoginIn, SsoProvidersOut, TokenUserOut, UserOut
from corp_os.services.audit import write_audit
from corp_os.services.auth import authenticate_user, get_current_user, issue_token_for_user
from corp_os.services.rate_limit import login_rate_limiter

router = APIRouter()


@router.post("/login", response_model=TokenUserOut)
def account_login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.auth_account_enabled:
        raise HTTPException(status_code=400, detail="账号登录未启用")

    client = request.client.host if request.client else "unknown"
    key = f"login:{client}:{body.username.strip().lower()}"
    if not login_rate_limiter.allow(key, limit=settings.login_rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")

    user = authenticate_user(db, body.username.strip(), body.password)
    if not user:
        write_audit(
            db,
            actor=body.username.strip() or None,
            action="auth.login_failed",
            resource_type="user",
            resource_id=body.username.strip() or None,
            detail={"client": client},
        )
        db.commit()
        raise HTTPException(status_code=401, detail="账号或密码错误")

    token = issue_token_for_user(user)
    write_audit(
        db,
        actor=user.username,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        detail={"client": client},
    )
    db.commit()
    return token


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/sso/providers", response_model=SsoProvidersOut)
def sso_providers() -> dict:
    """SSO capability discovery (DingTalk / OIDC are stubs until IdP is wired)."""
    settings = get_settings()
    return {
        "account_password": settings.auth_account_enabled,
        "dingtalk": {
            "enabled": settings.sso_dingtalk_enabled,
            "status": "ready" if settings.sso_dingtalk_enabled and settings.sso_dingtalk_app_key else "stub",
            "app_key_configured": bool(settings.sso_dingtalk_app_key),
        },
        "oidc": {
            "enabled": settings.sso_oidc_enabled,
            "status": "ready" if settings.sso_oidc_enabled and settings.sso_oidc_issuer else "stub",
            "issuer": settings.sso_oidc_issuer or None,
            "client_id": settings.sso_oidc_client_id or None,
        },
    }


@router.post("/sso/dingtalk")
def sso_dingtalk_login() -> None:
    settings = get_settings()
    if not settings.sso_dingtalk_enabled:
        raise HTTPException(status_code=501, detail="钉钉 SSO 未启用（CORP_OS_SSO_DINGTALK_ENABLED）")
    raise HTTPException(
        status_code=501,
        detail="钉钉 SSO 对接尚未实现；请使用账号密码登录。用户表已预留 dingtalk_userid。",
    )


@router.post("/sso/oidc")
def sso_oidc_login() -> None:
    settings = get_settings()
    if not settings.sso_oidc_enabled:
        raise HTTPException(status_code=501, detail="OIDC SSO 未启用（CORP_OS_SSO_OIDC_ENABLED）")
    raise HTTPException(
        status_code=501,
        detail="OIDC SSO 对接尚未实现；请使用账号密码登录。",
    )
