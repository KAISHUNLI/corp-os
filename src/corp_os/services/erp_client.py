"""HTTP client for company-er (ERP): read + write (CRUD).

Auth uses per-user ERP identity (step 9). corp-os role permissions gate
which actions a user may call; ERP RBAC still applies on the bound account.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any

import httpx

from corp_os.config import get_settings
from corp_os.models.iam import User
from corp_os.services.erp_identity import identity_denied_message, resolve_erp_identity
from corp_os.services.permissions import (
    can_use_erp_kind,
    can_use_erp_path,
    erp_perm_for_path,
    is_dangerous_erp_call,
    is_elevated,
    normalize_erp_rel_path,
)

logger = logging.getLogger(__name__)

_token_lock = threading.Lock()
_cached_tokens: dict[str, str] = {}
_ERP_TOKEN_PREFIX = "corp_os:erp_token:"


class ErpError(RuntimeError):
    pass


def erp_enabled() -> bool:
    settings = get_settings()
    return bool(settings.erp_enabled and settings.erp_base_url)


def clear_token_cache(erp_username: str | None = None) -> None:
    from corp_os.services.redis_client import get_redis

    with _token_lock:
        if erp_username:
            _cached_tokens.pop(erp_username, None)
        else:
            _cached_tokens.clear()
    client = get_redis()
    if client is None:
        return
    try:
        if erp_username:
            client.delete(f"{_ERP_TOKEN_PREFIX}{erp_username}")
        else:
            for key in client.scan_iter(match=f"{_ERP_TOKEN_PREFIX}*", count=100):
                client.delete(key)
    except Exception:  # noqa: BLE001
        logger.exception("Failed clearing ERP token cache in Redis")


def _token_from_cache(erp_username: str) -> str | None:
    from corp_os.services.redis_client import get_redis

    with _token_lock:
        local = _cached_tokens.get(erp_username)
    if local:
        return local
    client = get_redis()
    if client is None:
        return None
    try:
        val = client.get(f"{_ERP_TOKEN_PREFIX}{erp_username}")
        if val:
            with _token_lock:
                _cached_tokens[erp_username] = val
            return val
    except Exception:  # noqa: BLE001
        logger.exception("Redis ERP token get failed")
    return None


def _token_to_cache(erp_username: str, token: str) -> None:
    from corp_os.services.redis_client import get_redis

    with _token_lock:
        _cached_tokens[erp_username] = token
    client = get_redis()
    if client is None:
        return
    try:
        ttl = max(60, int(get_settings().erp_token_ttl_seconds or 3600))
        client.setex(f"{_ERP_TOKEN_PREFIX}{erp_username}", ttl, token)
    except Exception:  # noqa: BLE001
        logger.exception("Redis ERP token set failed")


def _base() -> str:
    return get_settings().erp_base_url.rstrip("/")


def _api(path: str) -> str:
    prefix = get_settings().erp_api_prefix.rstrip("/") or "/api/v1"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{_base()}{prefix}{path}"


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "code" in payload and "data" in payload:
        code = payload.get("code")
        if code not in (0, "0", None):
            raise ErpError(f"ERP 业务错误 code={code}: {payload.get('message')}")
        return payload.get("data")
    return payload


def health() -> dict[str, Any]:
    if not get_settings().erp_base_url:
        raise ErpError("未配置 CORP_OS_ERP_BASE_URL")
    with httpx.Client(timeout=get_settings().erp_timeout_seconds) as client:
        resp = client.get(_api("/health"))
        resp.raise_for_status()
        data = _unwrap(resp.json())
        if not isinstance(data, dict):
            raise ErpError("ERP health 响应格式异常")
        return data


def login_as(*, erp_username: str, erp_password: str) -> str:
    settings = get_settings()
    with httpx.Client(timeout=settings.erp_timeout_seconds) as client:
        resp = client.post(
            _api("/auth/login"),
            json={"username": erp_username, "password": erp_password},
        )
        if resp.status_code >= 400:
            raise ErpError(f"ERP 登录失败: {resp.status_code} {resp.text[:200]}")
        data = _unwrap(resp.json())
        if not isinstance(data, dict):
            raise ErpError("ERP 登录响应格式异常")
        token = data.get("access_token")
        if not token:
            raise ErpError("ERP 登录响应缺少 access_token")
    _token_to_cache(erp_username, str(token))
    return str(token)


def login() -> str:
    settings = get_settings()
    if not settings.erp_username or not settings.erp_password:
        raise ErpError("未配置 CORP_OS_ERP_USERNAME / CORP_OS_ERP_PASSWORD")
    return login_as(erp_username=settings.erp_username, erp_password=settings.erp_password)


def _auth_headers(*, erp_username: str, erp_password: str) -> dict[str, str]:
    token = _token_from_cache(erp_username)
    if not token:
        token = login_as(erp_username=erp_username, erp_password=erp_password)
    return {"Authorization": f"Bearer {token}"}


def _request_json(
    method: str,
    path: str,
    *,
    erp_username: str,
    erp_password: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    settings = get_settings()
    headers = _auth_headers(erp_username=erp_username, erp_password=erp_password)
    with httpx.Client(timeout=settings.erp_timeout_seconds) as client:
        resp = client.request(
            method.upper(),
            _api(path),
            headers=headers,
            params=params or None,
            json=json_body,
        )
        if resp.status_code == 401:
            clear_token_cache(erp_username)
            headers = _auth_headers(erp_username=erp_username, erp_password=erp_password)
            resp = client.request(
                method.upper(),
                _api(path),
                headers=headers,
                params=params or None,
                json=json_body,
            )
        if resp.status_code >= 400:
            raise ErpError(
                f"ERP {method.upper()} {path} 失败: {resp.status_code} {resp.text[:300]}"
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return _unwrap(resp.json())


def _get_json(
    path: str,
    *,
    erp_username: str,
    erp_password: str,
    params: dict[str, Any] | None = None,
) -> Any:
    return _request_json(
        "GET",
        path,
        erp_username=erp_username,
        erp_password=erp_password,
        params=params,
    )


# ----- Employees -----


def list_employees(
    *,
    erp_username: str,
    erp_password: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    data = _get_json(
        "/hr/employees",
        erp_username=erp_username,
        erp_password=erp_password,
        params=params,
    )
    return data if isinstance(data, dict) else {"items": data or [], "total": len(data or [])}


def get_employee(
    *,
    erp_username: str,
    erp_password: str,
    employee_id: int,
) -> dict[str, Any]:
    data = _get_json(
        f"/hr/employees/{employee_id}",
        erp_username=erp_username,
        erp_password=erp_password,
    )
    if not isinstance(data, dict):
        raise ErpError("员工详情格式异常")
    return data


def create_employee(
    *,
    erp_username: str,
    erp_password: str,
    name: str,
    department: str | None = None,
    title: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    status: str = "active",
    remark: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name.strip(), "status": status or "active"}
    if department:
        body["department"] = department
    if title:
        body["title"] = title
    if phone:
        body["phone"] = phone
    if email:
        body["email"] = email
    if remark:
        body["remark"] = remark
    data = _request_json(
        "POST",
        "/hr/employees",
        erp_username=erp_username,
        erp_password=erp_password,
        json_body=body,
    )
    if not isinstance(data, dict):
        raise ErpError("创建员工响应异常")
    return data


def update_employee(
    *,
    erp_username: str,
    erp_password: str,
    employee_id: int,
    **fields: Any,
) -> dict[str, Any]:
    current = get_employee(
        erp_username=erp_username,
        erp_password=erp_password,
        employee_id=employee_id,
    )
    body = {
        "name": fields.get("name") or current.get("name"),
        "department": fields.get("department", current.get("department")),
        "title": fields.get("title", current.get("title")),
        "phone": fields.get("phone", current.get("phone")),
        "email": fields.get("email", current.get("email")),
        "hire_date": fields.get("hire_date", current.get("hire_date")),
        "status": fields.get("status") or current.get("status") or "active",
        "remark": fields.get("remark", current.get("remark")),
    }
    data = _request_json(
        "PUT",
        f"/hr/employees/{employee_id}",
        erp_username=erp_username,
        erp_password=erp_password,
        json_body=body,
    )
    if not isinstance(data, dict):
        raise ErpError("更新员工响应异常")
    return data


def delete_employee(
    *,
    erp_username: str,
    erp_password: str,
    employee_id: int,
) -> dict[str, Any]:
    """Hard delete when ERP supports DELETE; otherwise mark resigned."""
    try:
        data = _request_json(
            "DELETE",
            f"/hr/employees/{employee_id}",
            erp_username=erp_username,
            erp_password=erp_password,
        )
        return data if isinstance(data, dict) else {"ok": True, "id": employee_id, "mode": "deleted"}
    except ErpError as exc:
        if "405" not in str(exc) and "Method Not Allowed" not in str(exc):
            raise
        updated = update_employee(
            erp_username=erp_username,
            erp_password=erp_password,
            employee_id=employee_id,
            status="resigned",
        )
        updated["_mode"] = "resigned_fallback"
        return updated


# ----- Products / warehouses / stock -----


def list_products(
    *,
    erp_username: str,
    erp_password: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    data = _get_json(
        "/products",
        erp_username=erp_username,
        erp_password=erp_password,
        params=params,
    )
    return data if isinstance(data, dict) else {"items": data or [], "total": len(data or [])}


def create_product(
    *,
    erp_username: str,
    erp_password: str,
    name: str,
    unit: str = "件",
    spec: str | None = None,
    sale_price: float | None = None,
    status: str = "active",
    remark: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name.strip(), "unit": unit or "件", "status": status or "active"}
    if spec:
        body["spec"] = spec
    if sale_price is not None:
        body["sale_price"] = sale_price
    if remark:
        body["remark"] = remark
    data = _request_json(
        "POST",
        "/products",
        erp_username=erp_username,
        erp_password=erp_password,
        json_body=body,
    )
    if not isinstance(data, dict):
        raise ErpError("创建产品响应异常")
    return data


def update_product(
    *,
    erp_username: str,
    erp_password: str,
    product_id: int,
    **fields: Any,
) -> dict[str, Any]:
    current = _get_json(
        f"/products/{product_id}",
        erp_username=erp_username,
        erp_password=erp_password,
    )
    if not isinstance(current, dict):
        raise ErpError("产品不存在或格式异常")
    body = {
        "name": fields.get("name") or current.get("name"),
        "spec": fields.get("spec", current.get("spec")),
        "unit": fields.get("unit") or current.get("unit") or "件",
        "sale_price": fields.get("sale_price", current.get("sale_price")),
        "status": fields.get("status") or current.get("status") or "active",
        "remark": fields.get("remark", current.get("remark")),
    }
    data = _request_json(
        "PUT",
        f"/products/{product_id}",
        erp_username=erp_username,
        erp_password=erp_password,
        json_body=body,
    )
    if not isinstance(data, dict):
        raise ErpError("更新产品响应异常")
    return data


def delete_product(
    *,
    erp_username: str,
    erp_password: str,
    product_id: int,
) -> dict[str, Any]:
    try:
        data = _request_json(
            "DELETE",
            f"/products/{product_id}",
            erp_username=erp_username,
            erp_password=erp_password,
        )
        return data if isinstance(data, dict) else {"ok": True, "id": product_id, "mode": "deleted"}
    except ErpError as exc:
        if "405" not in str(exc) and "Method Not Allowed" not in str(exc):
            raise
        updated = update_product(
            erp_username=erp_username,
            erp_password=erp_password,
            product_id=product_id,
            status="inactive",
        )
        updated["_mode"] = "inactive_fallback"
        return updated


def list_warehouses(
    *,
    erp_username: str,
    erp_password: str,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    data = _get_json(
        "/inventory/warehouses",
        erp_username=erp_username,
        erp_password=erp_password,
        params={"page": page, "page_size": page_size},
    )
    return data if isinstance(data, dict) else {"items": data or [], "total": len(data or [])}


def create_warehouse(
    *,
    erp_username: str,
    erp_password: str,
    name: str,
    address: str | None = None,
    status: str = "active",
    remark: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name.strip(), "status": status or "active"}
    if address:
        body["address"] = address
    if remark:
        body["remark"] = remark
    data = _request_json(
        "POST",
        "/inventory/warehouses",
        erp_username=erp_username,
        erp_password=erp_password,
        json_body=body,
    )
    if not isinstance(data, dict):
        raise ErpError("创建仓库响应异常")
    return data


def list_stock_balances(
    *,
    erp_username: str,
    erp_password: str,
    page: int = 1,
    page_size: int = 20,
    low_stock: bool | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if low_stock is not None:
        params["low_stock"] = str(low_stock).lower()
    data = _get_json(
        "/inventory/balances",
        erp_username=erp_username,
        erp_password=erp_password,
        params=params,
    )
    return data if isinstance(data, dict) else {"items": data or [], "total": len(data or [])}


def create_and_confirm_stock_in(
    *,
    erp_username: str,
    erp_password: str,
    warehouse_id: int,
    product_id: int,
    product_name: str,
    qty: float,
    unit: str = "件",
    in_date: str | None = None,
    remark: str | None = None,
) -> dict[str, Any]:
    body = {
        "warehouse_id": warehouse_id,
        "in_date": in_date or date.today().isoformat(),
        "remark": remark,
        "items": [
            {
                "product_id": product_id,
                "product_name": product_name,
                "unit": unit or "件",
                "qty": qty,
            }
        ],
    }
    created = _request_json(
        "POST",
        "/inventory/stock-ins",
        erp_username=erp_username,
        erp_password=erp_password,
        json_body=body,
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise ErpError("创建入库单失败")
    order_id = int(created["id"])
    confirmed = _request_json(
        "POST",
        f"/inventory/stock-ins/{order_id}/confirm",
        erp_username=erp_username,
        erp_password=erp_password,
    )
    return confirmed if isinstance(confirmed, dict) else created


# ----- Formatters -----


def format_employees_answer(payload: dict[str, Any]) -> str:
    items = payload.get("items") or []
    total = payload.get("total", len(items))
    if not items:
        return (
            "当前员工人数：0。\n"
            "已连通 company-er，员工表是空的。可在 ERP 里先录入员工后再查。"
        )
    lines = [f"当前员工人数：{total} 人。", f"明细（展示前 {len(items)} 条）："]
    for row in items:
        name = row.get("name") or "-"
        emp_no = row.get("emp_no") or "-"
        dept = row.get("department_name") or row.get("department") or "-"
        title = row.get("position_name") or row.get("title") or "-"
        status = row.get("status") or "-"
        eid = row.get("id")
        lines.append(f"- #{eid} {name}（{emp_no}）· {dept} / {title} · 状态 {status}")
    lines.append("说明：数据来自 ERP 实时接口，未写入 corp-os 知识库。")
    return "\n".join(lines)


def format_balances_answer(payload: dict[str, Any]) -> str:
    items = payload.get("items") or payload.get("data") or []
    if isinstance(payload.get("data"), dict):
        items = payload["data"].get("items") or items
    if not isinstance(items, list):
        items = []
    total = payload.get("total", len(items))
    if not items:
        return (
            "已连通 company-er，当前没有库存结存记录。\n"
            "可在 ERP 里做入库/期初后再查「库存」。"
        )
    lines = [f"从 company-er 查到库存结存共 {total} 条（展示前 {len(items)} 条）："]
    for row in items[:20]:
        product = row.get("product_name") or row.get("product_code") or row.get("sku") or "-"
        wh = row.get("warehouse_name") or row.get("warehouse_code") or "-"
        qty = row.get("qty") if row.get("qty") is not None else row.get("quantity")
        lines.append(f"- {product} @ {wh} · 数量 {qty}")
    lines.append("说明：数据来自 ERP 实时接口，未写入 corp-os 知识库。")
    return "\n".join(lines)


def format_products_answer(payload: dict[str, Any]) -> str:
    items = payload.get("items") or []
    total = payload.get("total", len(items))
    if not items:
        return "ERP 中暂无产品。可用 create_product 新建。"
    lines = [f"产品共 {total} 个（展示前 {len(items)} 条）："]
    for row in items[:20]:
        lines.append(
            f"- #{row.get('id')} {row.get('name')} · 单位 {row.get('unit') or '-'} · 状态 {row.get('status')}"
        )
    return "\n".join(lines)


def _require_identity(user: User):
    identity = resolve_erp_identity(user)
    if identity is None:
        raise ErpError(identity_denied_message(user))
    return identity


def _compress_erp_payload(data: Any, *, list_limit: int, max_chars: int) -> Any:
    """Shrink ERP JSON for LLM context: truncate list items and stringify budget."""
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list) and len(items) > list_limit:
            out = dict(data)
            out["items"] = items[:list_limit]
            out["_truncated"] = True
            out["_shown"] = list_limit
            data = out
    elif isinstance(data, list) and len(data) > list_limit:
        data = {
            "items": data[:list_limit],
            "total": len(data),
            "_truncated": True,
            "_shown": list_limit,
        }

    try:
        import json

        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(data)
    if len(text) <= max_chars:
        return data
    return {
        "_truncated": True,
        "_note": f"响应过长已截断（>{max_chars} 字符），请加筛选条件或查单个 ID",
        "preview": text[: max_chars - 80],
    }


def format_erp_call_answer(
    *,
    method: str,
    path: str,
    data: Any,
    identity_tag: str,
) -> str:
    import json

    try:
        body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        body = str(data)
    return f"ERP {method.upper()} {path} 成功。\n{body}\n{identity_tag}"


def gateway_call(
    *,
    user: User,
    db=None,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> str:
    """Generic ERP HTTP call for the agent (covers full OpenAPI surface)."""
    if not erp_enabled():
        return (
            "ERP 对接未启用。请在 .env 设置 CORP_OS_ERP_ENABLED=true，"
            "并配置 CORP_OS_ERP_BASE_URL（默认 http://127.0.0.1:8002）。"
        )

    settings = get_settings()
    method_u = (method or "GET").strip().upper()
    if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return f"不支持的 HTTP 方法：{method}"

    try:
        rel = normalize_erp_rel_path(path, api_prefix=settings.erp_api_prefix)
    except ValueError as exc:
        return f"非法 ERP 路径：{exc}"

    if is_dangerous_erp_call(method=method_u, rel_path=rel) and not is_elevated(user):
        return (
            f"出于安全考虑，禁止通过 Agent 调用 {method_u} {rel}。"
            "请使用 ERP 管理后台（需管理员权限）。"
        )

    if not can_use_erp_path(db, user, method=method_u, rel_path=rel):
        need = erp_perm_for_path(rel, write=method_u not in {"GET", "HEAD", "OPTIONS"})
        return (
            f"当前账号（{user.username}/{user.role_code}）无权调用 "
            f"{method_u} {rel}（需要权限 {need}）。"
        )

    if method_u != "GET" and body is None:
        # Allow DELETE without body; POST/PUT/PATCH usually need body but some action
        # endpoints are body-less — pass empty dict for those.
        if method_u in {"POST", "PUT", "PATCH"}:
            body = {}

    try:
        identity = _require_identity(user)
        eu, ep = identity.erp_username, identity.erp_password
        tag = f"（ERP 身份：{eu} · {identity.source}）"
        data = _request_json(
            method_u,
            rel,
            erp_username=eu,
            erp_password=ep,
            params=params if isinstance(params, dict) else None,
            json_body=body if method_u != "GET" else None,
        )
        compressed = _compress_erp_payload(
            data,
            list_limit=max(1, int(settings.erp_call_list_limit or 15)),
            max_chars=max(500, int(settings.erp_call_max_chars or 6000)),
        )
        return format_erp_call_answer(method=method_u, path=rel, data=compressed, identity_tag=tag)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ERP gateway %s %s failed for %s", method_u, rel, user.username)
        return (
            f"调用 company-er 失败：{exc}\n"
            "请确认 ERP 已启动，路径/参数正确，且绑定的 ERP 账号有对应权限。"
        )


def run_erp_tool(kind: str, *, user: User, db=None, **params: Any) -> str:
    """Named ERP action → Chinese answer. Supports read and write kinds."""
    if not erp_enabled():
        return (
            "ERP 对接未启用。请在 .env 设置 CORP_OS_ERP_ENABLED=true，"
            "并配置 CORP_OS_ERP_BASE_URL（默认 http://127.0.0.1:8002）。"
        )
    if not can_use_erp_kind(db, user, kind):
        return (
            f"当前账号（{user.username}/{user.role_code}）无权执行 ERP「{kind}」。"
            "需要管理员开通对应 erp.* 权限。"
        )
    try:
        identity = _require_identity(user)
        eu, ep = identity.erp_username, identity.erp_password
        tag = f"（ERP 身份：{eu} · {identity.source}）"

        if kind == "health":
            data = health()
            return (
                f"ERP 连通正常：status={data.get('status')} "
                f"app={data.get('app')} version={data.get('version')} "
                f"（corp-os 用户 {user.username}，ERP 身份 {eu}/{identity.source}）"
            )

        if kind == "employees":
            payload = list_employees(erp_username=eu, erp_password=ep, page_size=15, keyword=params.get("keyword"))
            return f"{format_employees_answer(payload)}\n{tag}"

        if kind == "employee_get":
            row = get_employee(erp_username=eu, erp_password=ep, employee_id=int(params["employee_id"]))
            return (
                f"员工 #{row.get('id')} {row.get('name')}（{row.get('emp_no')}）\n"
                f"部门 {row.get('department') or '-'} / 职位 {row.get('title') or '-'}\n"
                f"电话 {row.get('phone') or '-'} · 状态 {row.get('status')}\n{tag}"
            )

        if kind == "employee_create":
            row = create_employee(
                erp_username=eu,
                erp_password=ep,
                name=str(params["name"]),
                department=params.get("department"),
                title=params.get("title"),
                phone=params.get("phone"),
                email=params.get("email"),
                remark=params.get("remark"),
            )
            return (
                f"已在 ERP 创建员工：#{row.get('id')} {row.get('name')}（{row.get('emp_no')}）\n{tag}"
            )

        if kind == "employee_update":
            row = update_employee(
                erp_username=eu,
                erp_password=ep,
                employee_id=int(params["employee_id"]),
                **{k: v for k, v in params.items() if k != "employee_id" and v is not None},
            )
            return f"已更新员工 #{row.get('id')} {row.get('name')}，状态 {row.get('status')}\n{tag}"

        if kind == "employee_delete":
            row = delete_employee(
                erp_username=eu,
                erp_password=ep,
                employee_id=int(params["employee_id"]),
            )
            mode = row.get("_mode") or row.get("mode") or "deleted"
            if mode == "resigned_fallback":
                return (
                    f"当前 ERP 不支持物理删除，已将员工 #{row.get('id')} {row.get('name')} "
                    f"标记为离职（resigned）。\n{tag}"
                )
            return f"已删除员工 #{params.get('employee_id')}。\n{tag}"

        if kind == "inventory":
            payload = list_stock_balances(erp_username=eu, erp_password=ep, page_size=15)
            return f"{format_balances_answer(payload)}\n{tag}"

        if kind == "products":
            payload = list_products(
                erp_username=eu,
                erp_password=ep,
                page_size=15,
                keyword=params.get("keyword"),
            )
            return f"{format_products_answer(payload)}\n{tag}"

        if kind == "product_create":
            row = create_product(
                erp_username=eu,
                erp_password=ep,
                name=str(params["name"]),
                unit=str(params.get("unit") or "件"),
                spec=params.get("spec"),
                sale_price=params.get("sale_price"),
                remark=params.get("remark"),
            )
            return f"已创建产品：#{row.get('id')} {row.get('name')}（单位 {row.get('unit')}）\n{tag}"

        if kind == "product_update":
            row = update_product(
                erp_username=eu,
                erp_password=ep,
                product_id=int(params["product_id"]),
                **{k: v for k, v in params.items() if k != "product_id" and v is not None},
            )
            return f"已更新产品 #{row.get('id')} {row.get('name')}，状态 {row.get('status')}\n{tag}"

        if kind == "product_delete":
            row = delete_product(
                erp_username=eu,
                erp_password=ep,
                product_id=int(params["product_id"]),
            )
            mode = row.get("_mode") or row.get("mode") or "deleted"
            if mode == "inactive_fallback":
                return (
                    f"当前 ERP 不支持物理删除产品，已将 #{row.get('id')} {row.get('name')} "
                    f"设为停用（inactive）。\n{tag}"
                )
            return f"已删除产品 #{params.get('product_id')}。\n{tag}"

        if kind == "warehouse_create":
            row = create_warehouse(
                erp_username=eu,
                erp_password=ep,
                name=str(params["name"]),
                address=params.get("address"),
                remark=params.get("remark"),
            )
            return f"已创建仓库：#{row.get('id')} {row.get('name')}\n{tag}"

        if kind == "warehouses":
            payload = list_warehouses(erp_username=eu, erp_password=ep)
            items = payload.get("items") or []
            if not items:
                return f"暂无仓库。\n{tag}"
            lines = [f"仓库共 {payload.get('total', len(items))} 个："]
            for row in items:
                lines.append(f"- #{row.get('id')} {row.get('name')} · {row.get('status')}")
            lines.append(tag)
            return "\n".join(lines)

        if kind == "stock_in":
            row = create_and_confirm_stock_in(
                erp_username=eu,
                erp_password=ep,
                warehouse_id=int(params["warehouse_id"]),
                product_id=int(params["product_id"]),
                product_name=str(params.get("product_name") or "商品"),
                qty=float(params["qty"]),
                unit=str(params.get("unit") or "件"),
                remark=params.get("remark"),
            )
            return (
                f"已创建并确认入库单 #{row.get('id')} {row.get('order_no') or ''}，"
                f"状态 {row.get('status')}。\n{tag}"
            )

        return f"未知 ERP 操作：{kind}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("ERP tool %s failed for %s", kind, user.username)
        return (
            f"调用 company-er 失败：{exc}\n"
            "请确认 ERP 已启动，且该用户绑定的 ERP 账号有对应读写权限。"
        )
