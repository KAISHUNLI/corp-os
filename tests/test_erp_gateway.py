"""Tests for ERP generic gateway (path guard + permissions)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from corp_os.services.erp_client import gateway_call
from corp_os.services.permissions import (
    can_use_erp_path,
    erp_perm_for_path,
    is_dangerous_erp_call,
    normalize_erp_rel_path,
)


def test_normalize_erp_rel_path_strips_prefix_and_rejects_url():
    assert normalize_erp_rel_path("/api/v1/sales/orders") == "/sales/orders"
    assert normalize_erp_rel_path("sales/orders") == "/sales/orders"
    assert normalize_erp_rel_path("/hr/employees/3") == "/hr/employees/3"
    try:
        normalize_erp_rel_path("http://evil.example/api/v1/x")
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        normalize_erp_rel_path("/api/v1/../etc/passwd")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_erp_perm_for_path_modules():
    assert erp_perm_for_path("/sales/orders", write=False) == "erp.sales"
    assert erp_perm_for_path("/sales/orders", write=True) == "erp.sales.write"
    assert erp_perm_for_path("/hr/employees", write=False) == "erp.employees"
    assert erp_perm_for_path("/hr/leave-requests", write=False) == "erp.hr"
    assert erp_perm_for_path("/finance/vouchers", write=True) == "erp.finance.write"
    assert erp_perm_for_path("/products/1", write=False) == "erp.products"
    assert erp_perm_for_path("/analytics/gross-margin", write=False) == "erp.analytics"


def test_dangerous_erp_calls():
    assert is_dangerous_erp_call(method="POST", rel_path="/system/users/1/reset-password")
    assert is_dangerous_erp_call(method="POST", rel_path="/system/roles")
    assert is_dangerous_erp_call(method="PUT", rel_path="/system/roles/2")
    assert not is_dangerous_erp_call(method="GET", rel_path="/system/roles")
    assert not is_dangerous_erp_call(method="GET", rel_path="/sales/orders")


def test_can_use_erp_path_respects_role_defaults():
    employee = SimpleNamespace(username="alice", role_code="employee")
    finance = SimpleNamespace(username="finance01", role_code="finance")
    boss = SimpleNamespace(username="boss", role_code="boss")

    assert can_use_erp_path(None, employee, method="GET", rel_path="/health")
    assert not can_use_erp_path(None, employee, method="GET", rel_path="/sales/orders")
    assert can_use_erp_path(None, finance, method="GET", rel_path="/finance/receivables")
    assert can_use_erp_path(None, finance, method="POST", rel_path="/finance/vouchers")
    assert not can_use_erp_path(None, finance, method="GET", rel_path="/sales/orders")
    assert can_use_erp_path(None, boss, method="POST", rel_path="/sales/orders")


def test_gateway_call_permission_denied():
    user = SimpleNamespace(username="alice", role_code="employee", erp_username=None)
    with patch("corp_os.services.erp_client.erp_enabled", return_value=True):
        out = gateway_call(user=user, db=None, method="GET", path="/sales/orders")
    assert "无权" in out


def test_gateway_call_blocks_dangerous_for_non_elevated():
    user = SimpleNamespace(username="finance01", role_code="finance", erp_username="admin")
    with patch("corp_os.services.erp_client.erp_enabled", return_value=True):
        out = gateway_call(
            user=user,
            db=None,
            method="POST",
            path="/system/users/1/reset-password",
            body={},
        )
    assert "禁止" in out or "安全" in out


def test_gateway_call_success_compresses_list():
    user = SimpleNamespace(username="boss", role_code="boss", erp_username="admin")
    identity = MagicMock(erp_username="admin", erp_password="admin", source="map")
    payload = {"items": [{"id": i} for i in range(40)], "total": 40}
    with (
        patch("corp_os.services.erp_client.erp_enabled", return_value=True),
        patch("corp_os.services.erp_client._require_identity", return_value=identity),
        patch("corp_os.services.erp_client._request_json", return_value=payload),
        patch("corp_os.config.get_settings") as gs,
    ):
        settings = MagicMock()
        settings.erp_api_prefix = "/api/v1"
        settings.erp_call_list_limit = 5
        settings.erp_call_max_chars = 6000
        gs.return_value = settings
        out = gateway_call(user=user, db=None, method="GET", path="/sales/orders")
    assert "成功" in out
    assert "_truncated" in out or '"_shown": 5' in out or "_shown" in out
