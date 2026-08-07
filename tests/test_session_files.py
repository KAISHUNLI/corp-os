from corp_os.services.session_files import is_library_publish_intent


def test_library_publish_phrases():
    assert is_library_publish_intent("请上传到知识库")
    assert is_library_publish_intent("写入知识库")
    assert not is_library_publish_intent("帮我看看这份制度说什么")
    assert not is_library_publish_intent("仓库入库 10 个")


def test_boss_can_promote_personal_pptx_to_library():
    from types import SimpleNamespace

    from corp_os.models.iam import User
    from corp_os.services.session_files import _promote_for_library

    boss = User(
        id=1,
        username="boss",
        display_name="老板",
        role_code="boss",
        department_code="exec",
        is_dept_manager=False,
        is_active=True,
    )
    doc = SimpleNamespace(
        title="商务汇报.pptx",
        filename="商务汇报.pptx",
        category="other",
        visibility="private",
        visibility_target=None,
        text_excerpt="",
        full_text="",
    )
    assert _promote_for_library(doc, boss) is None
    assert doc.visibility == "company"
    assert doc.category == "tech"


def test_employee_personal_stays_session_only():
    from types import SimpleNamespace

    from corp_os.models.iam import User
    from corp_os.services.session_files import _promote_for_library

    emp = User(
        id=2,
        username="alice",
        display_name="张三",
        role_code="employee",
        department_code="delivery",
        is_dept_manager=False,
        is_active=True,
    )
    doc = SimpleNamespace(
        title="发票.png",
        filename="发票.png",
        category="other",
        visibility="private",
        visibility_target=None,
        text_excerpt="",
        full_text="",
    )
    msg = _promote_for_library(doc, emp)
    assert msg and "个人材料" in msg
    assert doc.visibility == "private"
