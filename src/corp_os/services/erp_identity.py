"""Map corp-os users to company-er credentials (step 9)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from corp_os.config import get_settings
from corp_os.models.iam import User
from corp_os.services.permissions import is_elevated

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErpIdentity:
    erp_username: str
    erp_password: str
    source: str  # mapped | service


def _parse_credential_map() -> dict[str, str]:
    raw = (get_settings().erp_credential_map or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid CORP_OS_ERP_CREDENTIAL_MAP JSON")
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if key is None or value is None:
            continue
        out[str(key).strip()] = str(value)
    return out


def resolve_erp_identity(user: User) -> ErpIdentity | None:
    """Resolve which ERP login to use for this corp-os user."""
    settings = get_settings()
    mode = (settings.erp_identity_mode or "hybrid").strip().lower()
    creds = _parse_credential_map()

    if mode == "service":
        if not settings.erp_username or not settings.erp_password:
            return None
        return ErpIdentity(
            erp_username=settings.erp_username,
            erp_password=settings.erp_password,
            source="service",
        )

    bound = (user.erp_username or "").strip()
    if bound and bound in creds:
        return ErpIdentity(erp_username=bound, erp_password=creds[bound], source="mapped")
    if bound and settings.erp_username == bound and settings.erp_password:
        # Bound name equals service account username.
        return ErpIdentity(
            erp_username=settings.erp_username,
            erp_password=settings.erp_password,
            source="mapped",
        )

    if mode == "mapped":
        return None

    # hybrid: elevated users may fall back to service account
    if is_elevated(user) and settings.erp_username and settings.erp_password:
        return ErpIdentity(
            erp_username=settings.erp_username,
            erp_password=settings.erp_password,
            source="service",
        )
    return None


def identity_denied_message(user: User) -> str:
    return (
        f"用户 {user.username} 未绑定可用的 ERP 账号（erp_username / 凭证映射）。"
        "请管理员在用户上设置 erp_username，并配置 CORP_OS_ERP_CREDENTIAL_MAP。"
    )
