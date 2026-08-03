from corp_os.models.iam import User
from corp_os.services.governance import (
    can_approve_request,
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


def test_personal_expense_no_approval():
    s = classify_sensitivity(category="invoice", visibility="private", title="住宿发票", kind="invoice")
    assert s == "personal"
    assert needs_approval(_user("employee"), s) is False


def test_policy_upload_needs_approval_for_employee():
    s = classify_sensitivity(category="policy", visibility="company", title="新考勤制度")
    assert s == "important"
    assert needs_approval(_user("employee"), s) is True
    assert needs_approval(_user("boss"), s) is False


def test_critical_only_finance_or_manager_can_submit():
    s = classify_sensitivity(category="hr", visibility="role", title="员工薪资表", text="薪资")
    assert s == "critical"
    ok, _ = can_upload(_user("employee"), category="hr", visibility="role", sensitivity=s)
    assert ok is False
    ok2, _ = can_upload(_user("finance"), category="hr", visibility="role", sensitivity=s)
    assert ok2 is True


def test_dept_manager_cannot_approve_critical():
    req = DocumentChangeRequest(
        id=1,
        action="create",
        status="pending",
        sensitivity="critical",
        title="薪资",
        category="hr",
        visibility="role",
        department_code="finance",
        requested_by="finance01",
    )
    mgr = _user("employee", "finance", manager=True)
    boss = _user("boss", "exec")
    assert can_approve_request(mgr, req) is False
    assert can_approve_request(boss, req) is True
