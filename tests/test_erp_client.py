from corp_os.services.erp_client import format_balances_answer, format_employees_answer, erp_enabled


def test_format_employees_answer():
    text = format_employees_answer(
        {
            "total": 1,
            "items": [
                {
                    "name": "张三",
                    "emp_no": "E001",
                    "department_name": "交付",
                    "position_name": "实施",
                    "status": "active",
                }
            ],
        }
    )
    assert "张三" in text
    assert "E001" in text


def test_format_balances_answer():
    text = format_balances_answer(
        {
            "total": 1,
            "items": [
                {
                    "product_name": "螺丝",
                    "warehouse_name": "主仓",
                    "qty": 12,
                }
            ],
        }
    )
    assert "螺丝" in text
    assert "主仓" in text


def test_erp_disabled_by_default(monkeypatch):
    monkeypatch.setenv("CORP_OS_ERP_ENABLED", "false")
    from corp_os.config import get_settings

    get_settings.cache_clear()
    assert erp_enabled() is False
    get_settings.cache_clear()
