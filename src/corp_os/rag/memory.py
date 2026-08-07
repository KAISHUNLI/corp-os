"""Short-term conversational memory within a chat session."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.models.rag import ChatMessage


def history_limit() -> int:
    return max(0, int(get_settings().chat_history_max_messages or 0))


def load_session_history(db: Session, session_id: int | None) -> list[dict[str, str]]:
    """Load prior user/assistant turns for LLM context (oldest → newest)."""
    limit = history_limit()
    if not session_id or limit <= 0:
        return []

    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role.in_(("user", "assistant")),
            )
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    out: list[dict[str, str]] = []
    for row in rows:
        content = (row.content or "").strip()
        if not content:
            continue
        # Cap very long turns to keep prompts bounded.
        if len(content) > 1200:
            content = content[:1200] + "…"
        out.append({"role": row.role, "content": content})
    return out


def format_history_block(history: list[dict[str, str]] | None) -> str:
    if not history:
        return ""
    lines = []
    for msg in history:
        role = "用户" if msg.get("role") == "user" else "助手"
        lines.append(f"{role}：{msg.get('content', '')}")
    return "\n".join(lines)


def recent_user_claims(history: list[dict[str, str]] | None, *, limit: int = 5) -> list[str]:
    """User lines that look like self-statements (我是… / 我叫…)."""
    if not history:
        return []
    claims: list[str] = []
    for msg in history:
        if msg.get("role") != "user":
            continue
        text = (msg.get("content") or "").strip()
        if text.startswith("我") and len(text) <= 80:
            claims.append(text)
    return claims[-limit:]
