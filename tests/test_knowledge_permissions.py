from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.services.permissions import can_view_document


def _user(username: str, department: str, role: str) -> User:
    return User(
        id=1,
        username=username,
        display_name=username,
        department_code=department,
        role_code=role,
        is_dept_manager=False,
        is_active=True,
    )


def test_finance_docs_hidden_from_employee():
    salary = Document(
        id=1,
        title="薪资表",
        filename="s.txt",
        stored_path="x",
        category="hr",
        visibility="role",
        visibility_target="finance",
        status="active",
        uploaded_by="finance01",
    )
    report = Document(
        id=2,
        title="财务报表",
        filename="r.txt",
        stored_path="x",
        category="other",
        visibility="department",
        visibility_target="finance",
        status="active",
        uploaded_by="finance01",
        department_code="finance",
    )

    alice = _user("alice", "delivery", "employee")
    finance = _user("finance01", "finance", "finance")
    boss = _user("boss", "exec", "boss")

    assert not can_view_document(alice, salary)
    assert not can_view_document(alice, report)
    assert can_view_document(finance, salary)
    assert can_view_document(finance, report)
    assert can_view_document(boss, salary)
    assert can_view_document(boss, report)


def test_legal_role_isolation():
    legal_doc = Document(
        id=3,
        title="法务清单",
        filename="a.txt",
        stored_path="x",
        category="policy",
        visibility="role",
        visibility_target="legal",
        status="active",
        uploaded_by="legal01",
    )
    alice = _user("alice", "delivery", "employee")
    legal = _user("legal01", "legal", "legal")
    boss = _user("boss", "exec", "boss")
    assert not can_view_document(alice, legal_doc)
    assert can_view_document(legal, legal_doc)
    assert can_view_document(boss, legal_doc)
