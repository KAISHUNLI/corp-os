"""Tests for tool-calling agent (step 8)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from corp_os.rag.agent import (
    TOOL_SPECS,
    _ToolContext,
    execute_tool,
    run_tool_agent,
    use_agent_mode,
)


def test_tool_specs_cover_core_capabilities():
    names = {t["function"]["name"] for t in TOOL_SPECS}
    assert "search_company_knowledge" in names
    assert "list_employees" in names
    assert "create_employee" in names
    assert "delete_employee" in names
    assert "check_erp_health" in names
    assert "erp_find_operations" in names
    assert "erp_call" in names
    assert "list_employees" in names
    assert "stock_in" in names
    assert "check_expense" not in names
    assert "list_pending_approvals" not in names
    assert "decide_approval" not in names
    assert "correct_attachment_kind" not in names
    assert "read_session_files" in names
    assert "publish_to_knowledge_base" in names
    assert "generate_word" in names
    assert "generate_powerpoint" in names
    assert "generate_markdown" in names
    assert "share_library_file" in names


def test_use_agent_mode_auto_requires_llm(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "off")
    monkeypatch.setenv("CORP_OS_AGENT_MODE", "auto")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod
    from corp_os.rag import agent as agent_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    assert use_agent_mode() is False

    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CORP_OS_LLM_API_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("CORP_OS_AGENT_MODE", "auto")
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    assert agent_mod.use_agent_mode() is True
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()


def test_execute_list_employees_calls_erp():
    ctx = _ToolContext(
        db=MagicMock(),
        user=SimpleNamespace(username="boss", role_code="boss", erp_username="admin"),
        session=SimpleNamespace(id=1),
        message="多少员工",
    )
    with (
        patch("corp_os.rag.agent.can_use_tool", return_value=True),
        patch("corp_os.rag.agent.run_erp_tool", return_value="当前员工人数：2 人。") as mock_erp,
    ):
        out = execute_tool("list_employees", {}, ctx)
    assert "2" in out
    mock_erp.assert_called_once()


def test_execute_tool_denied():
    ctx = _ToolContext(
        db=MagicMock(),
        user=SimpleNamespace(username="alice", role_code="employee"),
        session=SimpleNamespace(id=1),
        message="多少员工",
    )
    with patch("corp_os.rag.agent.can_use_tool", return_value=False):
        out = execute_tool("list_employees", {}, ctx)
    assert "无权" in out


def test_run_tool_agent_loop_with_mock_llm(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CORP_OS_LLM_API_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("CORP_OS_LLM_MODEL", "mock")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()

    turns = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "list_employees",
                    "args": {},
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="公司现在有 2 名员工。"),
    ]

    class FakeBound:
        def invoke(self, messages):
            if len(turns) == 2:
                assert isinstance(messages[0], SystemMessage)
                assert isinstance(messages[1], HumanMessage)
                assert messages[1].content == "我是老板"
                assert isinstance(messages[2], AIMessage)
                assert isinstance(messages[3], HumanMessage)
                assert messages[3].content == "多少员工"
            return turns.pop(0)

    class FakeModel:
        def bind_tools(self, *_args, **_kwargs):
            return FakeBound()

    user = SimpleNamespace(username="boss", display_name="老板", role_code="boss")
    session = SimpleNamespace(id=99)
    with (
        patch("corp_os.rag.agent.get_chat_model", return_value=FakeModel()),
        patch("corp_os.rag.agent.can_use_tool", return_value=True),
        patch("corp_os.rag.agent.run_erp_tool", return_value="当前员工人数：2 人。"),
    ):
        result = run_tool_agent(
            MagicMock(),
            user=user,
            session=session,
            message="多少员工",
            history=[
                {"role": "user", "content": "我是老板"},
                {"role": "assistant", "content": "知道了"},
            ],
        )

    assert "2" in result.answer
    assert result.tool_trace == ["list_employees"]
    assert result.action == "chat.agent"
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()


def test_sanitize_generated_links_replaces_hallucinated_ids():
    from corp_os.rag.agent import _sanitize_generated_links

    real = "/api/v1/chat/generated/aaaaaaaaaaaaaaaa"
    text = "已生成 PPT\n下载地址：/api/v1/chat/generated/bbbbbbbbbbbbbbbb"
    out = _sanitize_generated_links(text, [real])
    assert "bbbbbbbbbbbbbbbb" not in out
    assert "aaaaaaaaaaaaaaaa" in out


def test_sanitize_appends_missing_real_url():
    from corp_os.rag.agent import _sanitize_generated_links

    real = "/api/v1/chat/generated/cccccccccccccccc"
    out = _sanitize_generated_links("PPT 已就绪", [real])
    assert real in out


def test_sanitize_clears_empty_download_and_finalize_guards_false_claim():
    from corp_os.rag.agent import _ToolContext, _finalize_agent_answer, _sanitize_generated_links

    text = "新 PPT 已生成\n**下载地址：** ``\n请点「预览」或「下载」"
    cleaned = _sanitize_generated_links(text, [])
    assert "/api/v1/chat/generated/" not in cleaned

    ctx = _ToolContext(
        db=MagicMock(),
        user=MagicMock(),
        session=MagicMock(),
        message="生成ppt",
        generated_urls=[],
    )
    out = _finalize_agent_answer(text, ctx)
    assert "没有真正生成文件" in out
    assert "预览" not in out or "没有可预览" in out


def test_finalize_keeps_real_download_url():
    from corp_os.rag.agent import _ToolContext, _finalize_agent_answer

    real = "/api/v1/chat/generated/dddddddddddddddd"
    ctx = _ToolContext(
        db=MagicMock(),
        user=MagicMock(),
        session=MagicMock(),
        message="生成ppt",
        generated_urls=[real],
    )
    out = _finalize_agent_answer("PPT 做好了，请预览", ctx)
    assert real in out
