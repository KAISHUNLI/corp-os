"""OpenAPI-driven catalog of company-er ERP operations for agent discovery."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from corp_os.config import get_settings

logger = logging.getLogger(__name__)

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_cache_lock = threading.Lock()
_cached_ops: list["ErpOperation"] | None = None
_cached_at: float = 0.0
_CACHE_TTL_SECONDS = 300.0
_REDIS_CATALOG_KEY = "corp_os:erp_openapi_ops_v1"

# Chinese business phrases → English path / summary tokens (OpenAPI often English-only).
_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "销售订单": ("sales/orders", "sales_order", "order"),
    "销售": ("/sales/", "sales"),
    "采购订单": ("purchase/orders", "purchase_order"),
    "采购": ("/purchase/", "purchase"),
    "库存": ("/inventory/", "inventory", "stock"),
    "入库": ("stock-ins", "stock_in"),
    "出库": ("stock-outs", "stock_out"),
    "请假": ("leave-requests", "leave"),
    "考勤": ("attendance",),
    "员工": ("/hr/employees", "employee"),
    "客户": ("customers", "customer"),
    "供应商": ("suppliers", "supplier"),
    "毛利": ("gross-margin", "gross_margin", "margin"),
    "毛利率": ("gross-margin", "gross_margin"),
    "应收": ("receivables", "receivable"),
    "应付": ("payables", "payable"),
    "凭证": ("vouchers", "voucher"),
    "发票": ("invoices", "invoice"),
    "报表": ("reports", "report"),
    "分析": ("/analytics/", "analytics"),
    "工单": ("work-orders", "work_order"),
    "合同": ("contracts", "contract"),
    "报价": ("quotes", "quote"),
    "盘点": ("stocktakes", "stocktake"),
    "调拨": ("transfers", "transfer"),
}


@dataclass(frozen=True)
class ErpOperation:
    operation_id: str
    method: str
    path: str
    tag: str
    summary: str
    required_params: tuple[str, ...]
    required_body_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clear_catalog_cache() -> None:
    global _cached_ops, _cached_at
    with _cache_lock:
        _cached_ops = None
        _cached_at = 0.0
    try:
        from corp_os.services.redis_client import get_redis

        client = get_redis()
        if client is not None:
            client.delete(_REDIS_CATALOG_KEY)
    except Exception:  # noqa: BLE001
        logger.exception("Failed clearing ERP catalog cache in Redis")


def openapi_url() -> str:
    settings = get_settings()
    custom = (settings.erp_openapi_url or "").strip()
    if custom:
        return custom
    return f"{settings.erp_base_url.rstrip('/')}/openapi.json"


def _path_params(path: str) -> list[str]:
    return re.findall(r"\{([a-zA-Z0-9_]+)\}", path or "")


def _required_from_parameters(params: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for p in params or []:
        if not isinstance(p, dict):
            continue
        if p.get("required") and p.get("name"):
            out.append(str(p["name"]))
    return out


def _required_body_fields(spec: dict[str, Any]) -> list[str]:
    body = (spec.get("requestBody") or {}).get("content") or {}
    schema: dict[str, Any] | None = None
    for media in ("application/json", "application/x-www-form-urlencoded"):
        if media in body and isinstance(body[media], dict):
            schema = body[media].get("schema")
            break
    if not isinstance(schema, dict):
        return []
    # Prefer explicit required; ignore $ref expansion for catalog brevity.
    req = schema.get("required")
    if isinstance(req, list):
        return [str(x) for x in req]
    return []


def parse_openapi(doc: dict[str, Any]) -> list[ErpOperation]:
    """Build a flat operation index from an OpenAPI 3 document."""
    paths = doc.get("paths") or {}
    ops: list[ErpOperation] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            m = (method or "").lower()
            if m not in _HTTP_METHODS or not isinstance(spec, dict):
                continue
            tags = spec.get("tags") or ["untagged"]
            tag = str(tags[0]) if tags else "untagged"
            summary = str(spec.get("summary") or spec.get("operationId") or "").strip()
            op_id = str(spec.get("operationId") or f"{m}_{path}").strip()
            required = _path_params(str(path)) + _required_from_parameters(spec.get("parameters"))
            # de-dupe while preserving order
            seen: set[str] = set()
            req_unique: list[str] = []
            for name in required:
                if name not in seen:
                    seen.add(name)
                    req_unique.append(name)
            ops.append(
                ErpOperation(
                    operation_id=op_id,
                    method=m.upper(),
                    path=str(path),
                    tag=tag,
                    summary=summary,
                    required_params=tuple(req_unique),
                    required_body_fields=tuple(_required_body_fields(spec)),
                )
            )
    return ops


def fetch_openapi_document() -> dict[str, Any]:
    settings = get_settings()
    url = openapi_url()
    with httpx.Client(timeout=settings.erp_timeout_seconds) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or "paths" not in data:
        raise RuntimeError(f"OpenAPI 响应无效: {url}")
    return data


def load_operations(*, force_refresh: bool = False) -> list[ErpOperation]:
    global _cached_ops, _cached_at
    now = time.monotonic()
    with _cache_lock:
        if (
            not force_refresh
            and _cached_ops is not None
            and (now - _cached_at) < _CACHE_TTL_SECONDS
        ):
            return list(_cached_ops)

    if not force_refresh:
        cached = _load_ops_from_redis()
        if cached is not None:
            with _cache_lock:
                _cached_ops = cached
                _cached_at = time.monotonic()
                return list(cached)

    try:
        doc = fetch_openapi_document()
        ops = parse_openapi(doc)
    except Exception:
        logger.exception("Failed to load ERP OpenAPI catalog from %s", openapi_url())
        with _cache_lock:
            if _cached_ops is not None:
                return list(_cached_ops)
        raise
    _store_ops_to_redis(ops)
    with _cache_lock:
        _cached_ops = ops
        _cached_at = time.monotonic()
        return list(ops)


def _ops_from_dicts(rows: list[dict[str, Any]]) -> list[ErpOperation]:
    out: list[ErpOperation] = []
    for row in rows:
        out.append(
            ErpOperation(
                operation_id=str(row.get("operation_id") or ""),
                method=str(row.get("method") or ""),
                path=str(row.get("path") or ""),
                tag=str(row.get("tag") or ""),
                summary=str(row.get("summary") or ""),
                required_params=tuple(row.get("required_params") or ()),
                required_body_fields=tuple(row.get("required_body_fields") or ()),
            )
        )
    return out


def _load_ops_from_redis() -> list[ErpOperation] | None:
    import json

    try:
        from corp_os.services.redis_client import get_redis

        client = get_redis()
        if client is None:
            return None
        raw = client.get(_REDIS_CATALOG_KEY)
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, list):
            return None
        return _ops_from_dicts(data)
    except Exception:  # noqa: BLE001
        logger.exception("Redis ERP catalog get failed")
        return None


def _store_ops_to_redis(ops: list[ErpOperation]) -> None:
    import json

    try:
        from corp_os.services.redis_client import get_redis

        client = get_redis()
        if client is None:
            return
        payload = json.dumps([o.to_dict() for o in ops], ensure_ascii=False)
        client.setex(_REDIS_CATALOG_KEY, int(_CACHE_TTL_SECONDS), payload)
    except Exception:  # noqa: BLE001
        logger.exception("Redis ERP catalog set failed")


def _score_operation(op: ErpOperation, tokens: list[str]) -> int:
    blob = f"{op.tag} {op.summary} {op.path} {op.operation_id} {op.method}".lower()
    score = 0
    for tok in tokens:
        if not tok:
            continue
        if tok in blob:
            score += 3 if tok in (op.tag.lower(),) or tok in op.path.lower() else 1
        # prefer exact tag match boost
        if tok == op.tag.lower():
            score += 5
    return score


def find_operations(
    query: str,
    *,
    tag: str | None = None,
    limit: int = 15,
    operations: list[ErpOperation] | None = None,
) -> list[ErpOperation]:
    """Rank ERP operations by keyword overlap with query / optional tag filter."""
    ops = operations if operations is not None else load_operations()
    q = (query or "").strip().lower()
    tag_f = (tag or "").strip().lower()
    if tag_f:
        ops = [o for o in ops if tag_f in o.tag.lower()]
    if not q and not tag_f:
        return ops[: max(1, min(int(limit or 15), 30))]

    # split CJK-friendly: keep whole query + alphanumeric tokens + common separators
    tokens = [q] if q else []
    tokens.extend(re.findall(r"[a-z0-9_/-]+", q))
    for part in re.split(r"[\s,，。；;、/|]+", q):
        part = part.strip()
        if len(part) >= 2 and part not in tokens:
            tokens.append(part)
    # also add 2-gram CJK chunks for short Chinese queries
    cjk = re.findall(r"[\u4e00-\u9fff]+", q)
    for chunk in cjk:
        if chunk not in tokens:
            tokens.append(chunk)
        if len(chunk) >= 4:
            for i in range(len(chunk) - 1):
                bi = chunk[i : i + 2]
                if bi not in tokens:
                    tokens.append(bi)

    # Expand Chinese business aliases → English OpenAPI path tokens.
    alias_tokens: list[str] = []
    raw_q = (query or "").strip()
    for phrase, aliases in _QUERY_ALIASES.items():
        if phrase in raw_q or phrase in q:
            alias_tokens.extend(aliases)
    for chunk in cjk:
        for phrase, aliases in _QUERY_ALIASES.items():
            if chunk in phrase or phrase in chunk:
                alias_tokens.extend(aliases)
    for a in alias_tokens:
        if a.lower() not in tokens:
            tokens.append(a.lower())

    ranked: list[tuple[int, ErpOperation]] = []
    for op in ops:
        score = _score_operation(op, tokens)
        if score > 0 or (tag_f and not q):
            ranked.append((score, op))
    ranked.sort(key=lambda x: (-x[0], x[1].tag, x[1].path, x[1].method))
    lim = max(1, min(int(limit or 15), 30))
    return [op for _, op in ranked[:lim]]


def format_operations_answer(ops: list[ErpOperation], *, query: str = "") -> str:
    if not ops:
        return (
            f"未找到与「{query}」匹配的 ERP 接口。可换关键词，例如：销售订单、请假、库存结存、毛利率。"
        )
    lines = [f"找到 {len(ops)} 个相关 ERP 接口（先选一个再用 erp_call）："]
    for op in ops:
        req = ",".join(op.required_params) if op.required_params else "-"
        body = ",".join(op.required_body_fields) if op.required_body_fields else "-"
        lines.append(
            f"- [{op.tag}] {op.method} {op.path}\n"
            f"  说明：{op.summary or op.operation_id}\n"
            f"  路径/查询必填：{req}；body 必填：{body}"
        )
    return "\n".join(lines)
