"""Startup / additive schema helpers for security step 9."""

from __future__ import annotations

import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.models.iam import Role, User

logger = logging.getLogger(__name__)

_WEAK_SECRETS = {
    "",
    "change-me-in-production",
    "secret",
    "changeme",
    "corp-os",
}

_ROLE_PERMS = {
    "employee": "chat,upload.personal",
    "legal": "chat,upload.personal,governance.read",
    "finance": (
        "chat,upload.personal,finance.read,erp.inventory,erp.products,"
        "erp.health,governance.read"
    ),
    "boss": "*",
    "admin": "*",
}

# corp-os username -> erp_username for demo bootstrap
_DEMO_ERP_BINDINGS = {
    "boss": "admin",
    "admin": "admin",
}


def validate_security_settings() -> None:
    settings = get_settings()
    weak = (settings.secret_key or "").strip() in _WEAK_SECRETS or len(settings.secret_key or "") < 16
    if weak:
        msg = (
            "CORP_OS_SECRET_KEY 过弱或不安全。生产环境必须设置足够长的随机密钥。"
        )
        if (settings.env or "dev").lower() in {"prod", "production"}:
            raise RuntimeError(msg)
        logger.warning(msg)


def ensure_erp_username_column(db: Session) -> None:
    """Add users.erp_username if missing (dev / upgrade without full migrate)."""
    settings = get_settings()
    try:
        if settings.database_url.startswith("sqlite"):
            rows = db.execute(text("PRAGMA table_info(users)")).mappings().all()
            cols = {row["name"] for row in rows}
            if "erp_username" not in cols:
                db.execute(text("ALTER TABLE users ADD COLUMN erp_username VARCHAR(64)"))
                db.commit()
            return
        # PostgreSQL
        exists = db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='erp_username'"
            )
        ).scalar()
        if not exists:
            db.execute(text("ALTER TABLE users ADD COLUMN erp_username VARCHAR(64)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_users_erp_username ON users (erp_username)"))
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("ensure_erp_username_column failed")
        db.rollback()


def ensure_role_permissions(db: Session) -> None:
    """Upgrade demo role permission strings to include erp.* / governance.*."""
    changed = False
    for code, perms in _ROLE_PERMS.items():
        role = db.scalar(select(Role).where(Role.code == code))
        if role is None:
            continue
        if (role.permissions or "").strip() != perms:
            role.permissions = perms
            changed = True
    if changed:
        db.commit()


def ensure_demo_erp_bindings(db: Session) -> None:
    """Bind elevated demo users to ERP admin when erp_username empty."""
    changed = False
    for username, erp_username in _DEMO_ERP_BINDINGS.items():
        user = db.scalar(select(User).where(User.username == username))
        if user and not (user.erp_username or "").strip():
            user.erp_username = erp_username
            changed = True
    if changed:
        db.commit()
