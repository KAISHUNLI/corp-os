from corp_os.models.iam import User
from corp_os.rag.llm import build_rag_system_prompt
from corp_os.services.capabilities import policy_authoring_prompt_rules


def _user(*, manager: bool = False, role: str = "employee") -> User:
    return User(
        id=1,
        username="u",
        display_name="u",
        role_code=role,
        department_code="delivery",
        is_dept_manager=manager,
        is_active=True,
    )


def test_employee_prompt_forbids_policy_draft_offer():
    rules = policy_authoring_prompt_rules(_user())
    assert "普通员工" in rules
    assert "禁止" in rules
    prompt = build_rag_system_prompt(_user())
    assert "禁止" in prompt


def test_manager_prompt_allows_policy_draft_offer():
    rules = policy_authoring_prompt_rules(_user(manager=True))
    assert "主管" in rules or "老板" in rules
    assert "禁止" not in rules
