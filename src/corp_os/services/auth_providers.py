from __future__ import annotations

from corp_os.config import get_settings
from corp_os.services.dingtalk import build_dingtalk_login_url, dingtalk_enabled as dingtalk_creds_ready


def list_auth_providers() -> list[dict]:
    """Return login providers available to the current deployment."""
    settings = get_settings()
    providers: list[dict] = []

    if settings.auth_account_enabled:
        providers.append(
            {
                "id": "account",
                "name": "账号登录",
                "type": "account",
                "enabled": True,
                "login_url": None,
                "hint": "使用公司账号进入（不依赖任何即时通讯工具）",
            }
        )

    dingtalk_on = settings.dingtalk_enabled or dingtalk_creds_ready()
    dingtalk_url = None
    if dingtalk_creds_ready():
        try:
            dingtalk_url = build_dingtalk_login_url()
        except Exception:  # noqa: BLE001
            dingtalk_url = None
    providers.append(
        {
            "id": "dingtalk",
            "name": "钉钉",
            "type": "oauth",
            "enabled": bool(dingtalk_on and dingtalk_creds_ready()),
            "login_url": dingtalk_url,
            "hint": "可选。公司使用钉钉时再配置开启。",
            "mock_enabled": settings.dingtalk_mock_enabled,
        }
    )

    providers.append(
        {
            "id": "wecom",
            "name": "企业微信",
            "type": "oauth",
            "enabled": bool(settings.wecom_enabled and settings.wecom_corp_id and settings.wecom_secret),
            "login_url": None,
            "hint": "可选。配置企业微信后启用。",
        }
    )

    providers.append(
        {
            "id": "feishu",
            "name": "飞书",
            "type": "oauth",
            "enabled": bool(settings.feishu_enabled and settings.feishu_app_id and settings.feishu_app_secret),
            "login_url": None,
            "hint": "可选。配置飞书后启用。",
        }
    )

    return providers
