from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.models.iam import User


def dingtalk_enabled() -> bool:
    settings = get_settings()
    # Credentials present OR explicitly enabled with credentials
    return bool(settings.dingtalk_app_key and settings.dingtalk_app_secret)


def build_dingtalk_login_url(state: str = "corp-os") -> str:
    settings = get_settings()
    if not dingtalk_enabled():
        raise HTTPException(status_code=400, detail="未配置钉钉应用，请使用 mock 登录或配置 CORP_OS_DINGTALK_*")
    params = {
        "redirect_uri": settings.dingtalk_redirect_uri,
        "response_type": "code",
        "client_id": settings.dingtalk_app_key,
        "scope": "openid",
        "state": state,
        "prompt": "consent",
    }
    return f"https://login.dingtalk.com/oauth2/auth?{urlencode(params)}"


def mock_dingtalk_login(db: Session, dingtalk_userid: str) -> User:
    user = db.scalar(select(User).where(User.dingtalk_userid == dingtalk_userid, User.is_active.is_(True)))
    if not user:
        # also allow username as mock id
        user = db.scalar(select(User).where(User.username == dingtalk_userid, User.is_active.is_(True)))
    if not user:
        raise HTTPException(status_code=401, detail="钉钉用户未绑定内部账号")
    return user


async def exchange_dingtalk_code(db: Session, code: str) -> User:
    """Exchange DingTalk OAuth code for internal user.

    Real DingTalk API integration skeleton. In local/dev without credentials,
    use mock_dingtalk_login instead.
    """
    settings = get_settings()
    if not dingtalk_enabled():
        raise HTTPException(status_code=400, detail="未配置钉钉应用")

    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": settings.dingtalk_app_key,
                "clientSecret": settings.dingtalk_app_secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        if token_resp.status_code >= 400:
            raise HTTPException(status_code=401, detail=f"钉钉换票失败: {token_resp.text}")
        access_token = token_resp.json().get("accessToken")
        if not access_token:
            raise HTTPException(status_code=401, detail="钉钉未返回 accessToken")

        me_resp = await client.get(
            "https://api.dingtalk.com/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        if me_resp.status_code >= 400:
            raise HTTPException(status_code=401, detail=f"获取钉钉用户失败: {me_resp.text}")
        payload = me_resp.json()
        dingtalk_userid = payload.get("openId") or payload.get("unionId") or payload.get("nick")
        if not dingtalk_userid:
            raise HTTPException(status_code=401, detail="钉钉用户标识缺失")

    user = db.scalar(select(User).where(User.dingtalk_userid == str(dingtalk_userid)))
    if not user:
        raise HTTPException(
            status_code=403,
            detail=f"钉钉账号未绑定内部用户（dingtalk_userid={dingtalk_userid}）",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    return user
