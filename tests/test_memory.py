"""Tests for short-term conversational memory."""

from corp_os.rag.memory import format_history_block, recent_user_claims
from corp_os.services.capabilities import build_identity_answer


def test_format_history_block():
    text = format_history_block(
        [
            {"role": "user", "content": "我是老板"},
            {"role": "assistant", "content": "好的"},
        ]
    )
    assert "用户：我是老板" in text
    assert "助手：好的" in text


def test_recent_user_claims():
    claims = recent_user_claims(
        [
            {"role": "user", "content": "我是老板"},
            {"role": "assistant", "content": "收到"},
            {"role": "user", "content": "迟到怎么处理"},
        ]
    )
    assert claims == ["我是老板"]


def test_identity_answer_includes_chat_claims():
    user = type(
        "U",
        (),
        {
            "username": "alice",
            "display_name": "小美",
            "role_code": "employee",
            "department_code": "hr",
            "erp_username": None,
        },
    )()
    text = build_identity_answer(
        user,
        history=[{"role": "user", "content": "我是老板"}],
    )
    assert "alice" in text
    assert "我是老板" in text
