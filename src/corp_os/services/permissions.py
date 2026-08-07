"""Role permissions and tool / ERP access control (step 9+)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.models.document import Document
from corp_os.models.iam import Role, User

ELEVATED_ROLES = {"admin", "boss"}

# Default permissions when Role row is missing or empty.
_DEFAULT_ROLE_PERMS: dict[str, str] = {
    "employee": "chat,upload.personal,erp.health",
    "legal": "chat,upload.personal,governance.read,erp.health",
    "finance": (
        "chat,upload.personal,finance.read,erp.inventory,erp.products,"
        "erp.health,erp.finance,erp.finance.write,erp.analytics,erp.purchase,"
        "governance.read"
    ),
    "boss": "*",
    "admin": "*",
}

# Agent / graph tools → required permission code (* grants all).
TOOL_REQUIRED_PERM: dict[str, str] = {
    "search_company_knowledge": "chat",
    "list_employees": "erp.employees",
    "get_employee": "erp.employees",
    "create_employee": "erp.employees.write",
    "update_employee": "erp.employees.write",
    "delete_employee": "erp.employees.write",
    "list_inventory": "erp.inventory",
    "list_products": "erp.products",
    "list_warehouses": "erp.inventory",
    "create_product": "erp.products.write",
    "update_product": "erp.products.write",
    "delete_product": "erp.products.write",
    "create_warehouse": "erp.inventory.write",
    "stock_in": "erp.inventory.write",
    "check_erp_health": "erp.health",
    "erp_find_operations": "erp.health",
    "erp_call": "erp.health",
    "read_session_files": "chat",
    "share_library_file": "chat",
    "publish_to_knowledge_base": "chat",
    "generate_word": "chat",
    "generate_powerpoint": "chat",
    "generate_markdown": "chat",
}

# run_erp_tool kinds → permission
ERP_KIND_PERM: dict[str, str] = {
    "health": "erp.health",
    "employees": "erp.employees",
    "employee_get": "erp.employees",
    "employee_create": "erp.employees.write",
    "employee_update": "erp.employees.write",
    "employee_delete": "erp.employees.write",
    "inventory": "erp.inventory",
    "products": "erp.products",
    "product_create": "erp.products.write",
    "product_update": "erp.products.write",
    "product_delete": "erp.products.write",
    "warehouses": "erp.inventory",
    "warehouse_create": "erp.inventory.write",
    "stock_in": "erp.inventory.write",
}

# Relative API path prefix → (read_perm, write_perm). First match wins.
ERP_PATH_PERMS: list[tuple[str, str, str]] = [
    ("/hr/employees", "erp.employees", "erp.employees.write"),
    ("/hr/", "erp.hr", "erp.hr.write"),
    ("/sales/", "erp.sales", "erp.sales.write"),
    ("/crm/", "erp.sales", "erp.sales.write"),
    ("/purchase/", "erp.purchase", "erp.purchase.write"),
    ("/inventory/", "erp.inventory", "erp.inventory.write"),
    ("/products", "erp.products", "erp.products.write"),
    ("/finance/", "erp.finance", "erp.finance.write"),
    ("/mfg/", "erp.mfg", "erp.mfg.write"),
    ("/analytics/", "erp.analytics", "erp.analytics"),
    ("/system/", "erp.system", "erp.system"),
    ("/auth/", "erp.health", "erp.health"),
    ("/health", "erp.health", "erp.health"),
]


def is_elevated(user: User) -> bool:
    """Boss and admin can view all company knowledge."""
    return user.role_code in ELEVATED_ROLES


def role_permission_codes(db: Session | None, user: User) -> set[str]:
    raw = ""
    if db is not None:
        role = db.scalar(select(Role).where(Role.code == user.role_code))
        if role and role.permissions:
            raw = role.permissions
    if not raw:
        raw = _DEFAULT_ROLE_PERMS.get(user.role_code or "", "chat")
    return {p.strip() for p in raw.split(",") if p.strip()}


def has_permission(db: Session | None, user: User, code: str) -> bool:
    perms = role_permission_codes(db, user)
    if "*" in perms:
        return True
    if code in perms:
        return True
    # write implies read for same domain: erp.employees.write → erp.employees
    if code.endswith(".write"):
        return False
    write_code = f"{code}.write"
    return write_code in perms


def can_use_tool(db: Session | None, user: User, tool_name: str) -> bool:
    required = TOOL_REQUIRED_PERM.get(tool_name)
    if required is None:
        return False
    return has_permission(db, user, required)


def can_use_erp_kind(db: Session | None, user: User, kind: str) -> bool:
    required = ERP_KIND_PERM.get(kind)
    if required is None:
        return False
    return has_permission(db, user, required)


def normalize_erp_rel_path(path: str, *, api_prefix: str = "/api/v1") -> str:
    """Normalize to relative path under API prefix, e.g. /sales/orders."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("path 不能为空")
    if "://" in raw or raw.startswith("//"):
        raise ValueError("禁止传入完整 URL，请只传 API 路径，例如 /sales/orders")
    raw = raw.split("?", 1)[0].split("#", 1)[0].strip()
    if ".." in raw.split("/"):
        raise ValueError("非法 path")
    if not raw.startswith("/"):
        raw = f"/{raw}"
    prefix = (api_prefix or "/api/v1").rstrip("/") or "/api/v1"
    if raw == prefix or raw.startswith(prefix + "/"):
        raw = raw[len(prefix) :] or "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    # collapse duplicate slashes
    while "//" in raw:
        raw = raw.replace("//", "/")
    return raw


def erp_perm_for_path(rel_path: str, *, write: bool) -> str:
    """Map relative ERP path to corp-os permission code."""
    path = rel_path if rel_path.startswith("/") else f"/{rel_path}"
    for prefix, read_perm, write_perm in ERP_PATH_PERMS:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return write_perm if write else read_perm
        elif path == prefix or path.startswith(prefix + "/"):
            return write_perm if write else read_perm
    return "erp.system" if write else "erp.health"


def can_use_erp_path(
    db: Session | None,
    user: User,
    *,
    method: str,
    rel_path: str,
) -> bool:
    write = (method or "GET").upper() not in {"GET", "HEAD", "OPTIONS"}
    required = erp_perm_for_path(rel_path, write=write)
    return has_permission(db, user, required)


def is_dangerous_erp_call(*, method: str, rel_path: str) -> bool:
    """Hard-blocked unless elevated (password reset / role writes)."""
    m = (method or "").upper()
    p = rel_path if rel_path.startswith("/") else f"/{rel_path}"
    if m == "POST" and "/system/users/" in p and p.endswith("/reset-password"):
        return True
    if m in {"POST", "PUT", "PATCH", "DELETE"} and (
        p == "/system/roles" or p.startswith("/system/roles/")
    ):
        return True
    return False


def tool_denied_message(tool_name: str) -> str:
    required = TOOL_REQUIRED_PERM.get(tool_name, "?")
    return (
        f"当前账号无权使用工具「{tool_name}」（需要权限 {required}）。"
        "如需开通请联系管理员调整角色权限。"
    )


def can_view_document(user: User, doc: Document) -> bool:
    if is_elevated(user):
        return True
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
        allowed = {x.strip() for x in target.split(",") if x.strip()}
        return bool(user.role_code and user.role_code in allowed)

    if visibility == "private":
        return False

    return False
