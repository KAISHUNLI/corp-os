from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from corp_os.models.iam import User
from corp_os.models.rag import ChatAttachment, ChatMessage, ChatSession
from corp_os.rag.graph import run_chat_graph
from corp_os.rag.llm import answer_with_rag
from corp_os.rag.memory import load_session_history
from corp_os.services.audit import write_audit


def get_or_create_session(db: Session, user: User, session_id: int | None = None) -> ChatSession:
    if session_id:
        session = db.get(ChatSession, session_id)
        if not session or session.user_username != user.username:
            raise ValueError("会话不存在")
        return session
    session = ChatSession(user_username=user.username, title="新对话")
    db.add(session)
    db.flush()
    return session


def build_answer(question: str, hits: list[dict]) -> str:
    """RAG answer: LLM when configured, else template."""
    return answer_with_rag(question, hits)


def attach_to_session(
    db: Session,
    *,
    user: User,
    session_id: int | None,
    document_id: int,
    kind: str,
    label: str,
) -> ChatSession:
    session = get_or_create_session(db, user, session_id)
    db.add(
        ChatAttachment(
            session_id=session.id,
            document_id=document_id,
            kind=kind,
            label=label,
            uploaded_by=user.username,
        )
    )
    db.add(
        ChatMessage(
            session_id=session.id,
            role="system",
            content=f"已收到文件：[{kind}] {label}（暂存，未入库）",
        )
    )
    db.flush()
    return session


def chat_with_rag(
    db: Session,
    *,
    user: User,
    message: str,
    session_id: int | None = None,
) -> dict:
    message = (message or "").strip()
    if not message:
        raise ValueError("消息不能为空")

    session = get_or_create_session(db, user, session_id)
    if session.title == "新对话":
        session.title = message[:40]

    # Load prior turns before appending the current user message.
    history = load_session_history(db, session.id)
    db.add(ChatMessage(session_id=session.id, role="user", content=message))

    result = run_chat_graph(
        db,
        user=user,
        session=session,
        message=message,
        history=history,
    )
    answer = result.get("answer") or ""
    citations = list(result.get("citations") or [])
    action = result.get("action") or "chat.rag"

    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            content=answer,
            citations_json=json.dumps(citations, ensure_ascii=False),
        )
    )
    session.updated_at = datetime.now(timezone.utc)
    write_audit(
        db,
        actor=user.username,
        action=action,
        resource_type="chat_session",
        resource_id=str(session.id),
        detail={
            "question": message,
            "hit_count": len(citations),
            "route": result.get("route"),
            "intent": result.get("intent"),
            "intent_confidence": result.get("intent_confidence"),
        },
    )
    db.commit()
    db.refresh(session)

    return {
        "session_id": session.id,
        "answer": answer,
        "citations": citations,
    }


def list_messages(db: Session, *, user: User, session_id: int) -> list[dict]:
    session = get_or_create_session(db, user, session_id)
    rows = list(
        db.scalars(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.id.asc())
        )
    )
    out = []
    for row in rows:
        out.append(
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "citations": json.loads(row.citations_json or "[]"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return out


def list_sessions(db: Session, *, user: User, limit: int = 50) -> list[dict]:
    """Recent chat sessions for the current user (does not delete anything)."""
    limit = max(1, min(int(limit or 50), 100))
    rows = list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.user_username == user.username)
            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": row.id,
            "title": row.title or "新对话",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def delete_session(db: Session, *, user: User, session_id: int) -> None:
    """Delete a chat session and its messages/attachments (owner only)."""
    session = db.get(ChatSession, session_id)
    if not session or session.user_username != user.username:
        raise ValueError("会话不存在")

    db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    db.execute(delete(ChatAttachment).where(ChatAttachment.session_id == session_id))
    db.delete(session)
    write_audit(
        db,
        actor=user.username,
        action="chat.session_delete",
        resource_type="chat_session",
        resource_id=str(session_id),
        detail={"title": session.title},
    )
    db.commit()
