from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.db import get_db
from corp_os.models.iam import User
from corp_os.services.security import create_access_token, decode_access_token, hash_password, verify_password


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username, User.is_active.is_(True)))


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def issue_token_for_user(user: User) -> dict:
    token = create_access_token(username=user.username)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "department_code": user.department_code,
        "role_code": user.role_code,
        "access_token": token,
        "token_type": "bearer",
    }


def _bearer_username(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    try:
        payload = decode_access_token(value.strip())
    except ValueError:
        return None
    return str(payload["sub"])


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    username = _bearer_username(authorization)
    if not username:
        raise HTTPException(status_code=401, detail="请先登录（缺少或无效的 Bearer token）")
    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


def ensure_password_column(db: Session) -> None:
    """SQLite-friendly additive migration for older local DBs."""
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        return
    rows = db.execute(text("PRAGMA table_info(users)")).mappings().all()
    cols = {row["name"] for row in rows}
    if "password_hash" not in cols:
        db.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
        db.commit()


def ensure_demo_passwords(db: Session) -> None:
    """Fill missing password hashes so demo accounts remain usable after upgrades."""
    settings = get_settings()
    users = list(db.scalars(select(User)))
    changed = False
    for user in users:
        if not user.password_hash:
            user.password_hash = hash_password(settings.demo_password)
            changed = True
    if changed:
        db.commit()
