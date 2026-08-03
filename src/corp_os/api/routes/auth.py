from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.db import get_db
from corp_os.models.iam import User
from corp_os.schemas import LoginIn, TokenUserOut, UserOut
from corp_os.services.auth import authenticate_user, get_current_user, issue_token_for_user
from corp_os.services.auth_providers import list_auth_providers
from corp_os.services.dingtalk import (
    dingtalk_enabled,
    exchange_dingtalk_code,
    mock_dingtalk_login,
)

router = APIRouter()


class AuthProviderOut(BaseModel):
    id: str
    name: str
    type: str
    enabled: bool
    login_url: str | None = None
    hint: str | None = None
    mock_enabled: bool | None = None


class AuthProvidersOut(BaseModel):
    providers: list[AuthProviderOut]
    default_provider: str = "account"
    demo_password_hint: str | None = None


class DingTalkMockIn(BaseModel):
    dingtalk_userid: str = Field(default="ding_alice")


@router.get("/providers", response_model=AuthProvidersOut)
def auth_providers() -> AuthProvidersOut:
    settings = get_settings()
    providers = [AuthProviderOut(**item) for item in list_auth_providers()]
    default = "account"
    if not any(p.id == "account" and p.enabled for p in providers):
        enabled = next((p.id for p in providers if p.enabled), "account")
        default = enabled
    hint = settings.demo_password if settings.env == "dev" else None
    return AuthProvidersOut(
        providers=providers,
        default_provider=default,
        demo_password_hint=hint,
    )


@router.post("/login", response_model=TokenUserOut)
def account_login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.auth_account_enabled:
        raise HTTPException(status_code=400, detail="账号登录未启用")
    user = authenticate_user(db, body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return issue_token_for_user(user)


@router.post("/dingtalk/mock", response_model=TokenUserOut)
def dingtalk_mock(body: DingTalkMockIn, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.dingtalk_mock_enabled:
        raise HTTPException(status_code=400, detail="钉钉 mock 登录未启用")
    user = mock_dingtalk_login(db, body.dingtalk_userid)
    return issue_token_for_user(user)


@router.get("/dingtalk/callback", response_model=TokenUserOut)
async def dingtalk_callback(code: str = Query(...), db: Session = Depends(get_db)) -> dict:
    if not dingtalk_enabled():
        raise HTTPException(status_code=400, detail="未配置钉钉，请使用账号登录或其他已启用的方式")
    user = await exchange_dingtalk_code(db, code)
    return issue_token_for_user(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
