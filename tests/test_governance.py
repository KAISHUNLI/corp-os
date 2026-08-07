from corp_os.models.iam import User
from corp_os.services.governance import (
    can_approve_request,
    can_own_category,
    can_upload,
    classify_sensitivity,
    needs_approval,
)
from corp_os.models.governance import DocumentChangeRequest


def _user(role: str, dept: str = "delivery", manager: bool = False) -> User:
    return User(
        id=1,
        username="u",
        display_name="u",
        role_code=role,
        department_code=dept,
        is_dept_manager=manager,
        is_active=True,
    )


def test_employee_personal_is_session_only_not_company_library():
    """员工上传像豆包：只服务对话，不走公司知识入库权限。"""
    from corp_os.services.governance import is_session_ephemeral_uploader

    assert is_session_ephemeral_uploader(_user("employee")) is True
    assert is_session_ephemeral_uploader(_user("employee", manager=True)) is False
    s = classify_sensitivity(category="invoice", visibility="private", title="住宿发票", kind="invoice")
    ok, _ = can_upload(_user("employee"), category="invoice", visibility="private", sensitivity=s)
    assert ok is True
    assert needs_approval(_user("employee"), s) is False


def test_employee_cannot_upload_company_knowledge():
    s = classify_sensitivity(category="policy", visibility="company", title="新考勤制度")
    assert s == "important"
    ok, reason = can_upload(_user("employee"), category="policy", visibility="company", sensitivity=s)
    assert ok is False
    assert "主管" in reason


def test_hr_manager_can_upload_admin_policy():
    s = classify_sensitivity(category="policy", visibility="company", title="新考勤制度")
    ok, _ = can_upload(_user("employee", "hr", manager=True), category="policy", visibility="company", sensitivity=s)
    assert ok is True
    assert needs_approval(_user("employee", "hr", manager=True), s) is True
    assert needs_approval(_user("boss"), s) is False


def test_delivery_manager_cannot_upload_hr_policy():
    s = classify_sensitivity(category="policy", visibility="company", title="行政考勤")
    ok, reason = can_upload(
        _user("employee", "delivery", manager=True),
        category="policy",
        visibility="company",
        sensitivity=s,
    )
    assert ok is False
    assert "人事" in reason or "主管" in reason


def test_delivery_manager_can_upload_tech():
    s = classify_sensitivity(category="tech", visibility="company", title="接口规范")
    assert can_own_category(_user("employee", "delivery", manager=True), category="tech", visibility="company")
    ok, _ = can_upload(
        _user("employee", "delivery", manager=True),
        category="tech",
        visibility="company",
        sensitivity=s,
    )
    assert ok is True


def test_critical_requires_hr_owner_or_boss():
    s = classify_sensitivity(category="hr", visibility="role", title="员工薪资表", text="薪资")
    assert s == "critical"
    ok, _ = can_upload(_user("employee"), category="hr", visibility="role", sensitivity=s)
    assert ok is False
    ok_fin, _ = can_upload(_user("finance", "finance", manager=True), category="hr", visibility="role", sensitivity=s)
    assert ok_fin is False
    ok_hr, _ = can_upload(_user("employee", "hr", manager=True), category="hr", visibility="role", sensitivity=s)
    assert ok_hr is True


def test_dept_manager_cannot_approve_critical():
    req = DocumentChangeRequest(
        id=1,
        action="create",
        status="pending",
        sensitivity="critical",
        title="薪资",
        category="hr",
        visibility="role",
        department_code="hr",
        requested_by="hr01",
    )
    mgr = _user("employee", "hr", manager=True)
    boss = _user("boss", "exec")
    assert can_approve_request(mgr, req) is False
    assert can_approve_request(boss, req) is True
