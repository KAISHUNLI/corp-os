from __future__ import annotations

from corp_os.models.document import Document
from corp_os.models.iam import User


ELEVATED_ROLES = {"admin", "boss"}


def is_elevated(user: User) -> bool:
    """Boss and admin can view all company knowledge."""
    return user.role_code in ELEVATED_ROLES


def can_view_document(user: User, doc: Document) -> bool:
    # 老板 / 管理员：全部可见
    if is_elevated(user):
        return True
    # 上传者本人可见
    if doc.uploaded_by == user.username:
        return True

    visibility = (doc.visibility or "company").strip()
    target = (doc.visibility_target or "").strip()

    if visibility == "company":
        return True

    if visibility == "department":
        dept = target or (doc.department_code or "")
        allowed = {x.strip() for x in dept.split(",") if x.strip()}
        return bool(user.department_code and user.department_code in allowed)

    if visibility == "role":
        # support multi roles: "finance,hr"
        allowed = {x.strip() for x in target.split(",") if x.strip()}
        return bool(user.role_code and user.role_code in allowed)

    if visibility == "private":
        return False

    return False
