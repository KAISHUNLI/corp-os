"""Step 9: permissions, ERP identity, rate limit."""

from types import SimpleNamespace

from corp_os.services.erp_identity import resolve_erp_identity
from corp_os.services.permissions import can_use_erp_kind, can_use_tool


def test_employee_cannot_list_employees():
    user = SimpleNamespace(username="alice", role_code="employee")
    assert can_use_tool(None, user, "list_employees") is False
    assert can_use_erp_kind(None, user, "employees") is False
    assert can_use_tool(None, user, "search_company_knowledge") is True


def test_boss_can_use_erp_tools():
    user = SimpleNamespace(username="boss", role_code="boss")
    assert can_use_tool(None, user, "list_employees") is True
    assert can_use_tool(None, user, "create_employee") is True
    assert can_use_tool(None, user, "stock_in") is True
    assert can_use_tool(None, user, "list_inventory") is True
    assert can_use_erp_kind(None, user, "inventory") is True


def test_employee_cannot_write_erp():
    user = SimpleNamespace(username="alice", role_code="employee")
    assert can_use_tool(None, user, "create_employee") is False
    assert can_use_erp_kind(None, user, "employee_create") is False


def test_finance_inventory_but_not_employees():
    user = SimpleNamespace(username="finance01", role_code="finance")
    assert can_use_erp_kind(None, user, "inventory") is True
    assert can_use_erp_kind(None, user, "employees") is False


def test_resolve_mapped_identity(monkeypatch):
    monkeypatch.setenv("CORP_OS_ERP_IDENTITY_MODE", "mapped")
    monkeypatch.setenv("CORP_OS_ERP_CREDENTIAL_MAP", '{"admin":"admin"}')
    from corp_os.config import get_settings

    get_settings.cache_clear()
    user = SimpleNamespace(username="boss", role_code="boss", erp_username="admin")
    identity = resolve_erp_identity(user)
    assert identity is not None
    assert identity.erp_username == "admin"
    assert identity.source == "mapped"
    get_settings.cache_clear()


def test_resolve_unmapped_employee(monkeypatch):
    monkeypatch.setenv("CORP_OS_ERP_IDENTITY_MODE", "mapped")
    monkeypatch.setenv("CORP_OS_ERP_CREDENTIAL_MAP", '{"admin":"admin"}')
    from corp_os.config import get_settings

    get_settings.cache_clear()
    user = SimpleNamespace(username="alice", role_code="employee", erp_username=None)
    assert resolve_erp_identity(user) is None
    get_settings.cache_clear()


def test_rate_limiter_blocks():
    from corp_os.services.rate_limit import MemoryRateLimiter

    limiter = MemoryRateLimiter()
    assert limiter.allow("k", limit=2) is True
    assert limiter.allow("k", limit=2) is True
    assert limiter.allow("k", limit=2) is False


def test_rate_limiter_uses_memory_when_redis_disabled(monkeypatch):
    monkeypatch.setenv("CORP_OS_REDIS_ENABLED", "false")
    from corp_os.config import get_settings
    from corp_os.services.rate_limit import RateLimiter
    from corp_os.services.redis_client import reset_redis_client

    get_settings.cache_clear()
    reset_redis_client()
    limiter = RateLimiter()
    assert limiter.allow("mem-only", limit=1) is True
    assert limiter.allow("mem-only", limit=1) is False
    get_settings.cache_clear()
    reset_redis_client()
