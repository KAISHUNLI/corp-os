"""Tests for ERP OpenAPI catalog discovery."""

from __future__ import annotations

from corp_os.services.erp_catalog import (
    clear_catalog_cache,
    find_operations,
    format_operations_answer,
    parse_openapi,
)


SAMPLE_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "company-er", "version": "0.1.0"},
    "paths": {
        "/api/v1/sales/orders": {
            "get": {
                "tags": ["销售订单"],
                "summary": "销售订单列表",
                "operationId": "list_sales_orders",
                "parameters": [
                    {"name": "page", "in": "query", "required": False},
                    {"name": "keyword", "in": "query", "required": False},
                ],
            },
            "post": {
                "tags": ["销售订单"],
                "summary": "创建销售订单",
                "operationId": "create_sales_order",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["customer_id"],
                                "properties": {"customer_id": {"type": "integer"}},
                            }
                        }
                    }
                },
            },
        },
        "/api/v1/hr/leave-requests": {
            "get": {
                "tags": ["请假申请"],
                "summary": "请假列表",
                "operationId": "list_leave",
            },
            "post": {
                "tags": ["请假申请"],
                "summary": "新建请假",
                "operationId": "create_leave",
            },
        },
        "/api/v1/analytics/gross-margin": {
            "get": {
                "tags": ["经营分析"],
                "summary": "毛利率分析",
                "operationId": "gross_margin",
            }
        },
        "/api/v1/health": {
            "get": {
                "tags": ["健康"],
                "summary": "健康检查",
                "operationId": "health",
            }
        },
    },
}


def test_parse_openapi_builds_operations():
    ops = parse_openapi(SAMPLE_OPENAPI)
    assert len(ops) == 6
    paths = {(o.method, o.path) for o in ops}
    assert ("GET", "/api/v1/sales/orders") in paths
    assert ("POST", "/api/v1/sales/orders") in paths
    create = next(o for o in ops if o.operation_id == "create_sales_order")
    assert "customer_id" in create.required_body_fields


def test_find_operations_sales_orders():
    ops = parse_openapi(SAMPLE_OPENAPI)
    hits = find_operations("销售订单", operations=ops, limit=10)
    assert hits
    assert any("/sales/orders" in h.path for h in hits)


def test_find_operations_leave():
    ops = parse_openapi(SAMPLE_OPENAPI)
    hits = find_operations("请假", operations=ops)
    assert hits
    assert any("leave" in h.path for h in hits)


def test_find_operations_tag_filter():
    ops = parse_openapi(SAMPLE_OPENAPI)
    hits = find_operations("", tag="经营分析", operations=ops)
    assert len(hits) == 1
    assert "gross-margin" in hits[0].path


def test_find_operations_gross_margin_alias():
    ops = parse_openapi(SAMPLE_OPENAPI)
    hits = find_operations("毛利率", operations=ops)
    assert hits
    assert any("gross-margin" in h.path for h in hits)

    ops = parse_openapi(SAMPLE_OPENAPI)
    hits = find_operations("毛利率", operations=ops)
    text = format_operations_answer(hits, query="毛利率")
    assert "erp_call" in text
    assert "GET" in text


def test_clear_catalog_cache_noop():
    clear_catalog_cache()
