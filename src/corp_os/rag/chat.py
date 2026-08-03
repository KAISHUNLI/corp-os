from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.models.iam import User
from corp_os.models.rag import ChatAttachment, ChatMessage, ChatSession
from corp_os.rag.store import retrieve
from corp_os.services.audit import write_audit
from corp_os.services.expense_check import check_expense, is_expense_intent
from corp_os.services.governance import decide_change_request, list_pending_for_approver


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
    if not hits:
        return (
            "我在你有权限的公司资料里没有检索到足够相关的内容。\n"
            "你可以补充上传相关制度/通知，或换个问法（例如：迟到、考勤、处分、报销）。"
        )

    event_keywords = ("迟到", "早退", "旷工", "违规", "泄密", "请假")
    is_event = any(k in question for k in event_keywords)

    lines: list[str] = []
    if is_event:
        lines.append(f"针对「{question}」，根据公司制度中与你权限相关的条款，整理如下：")
    else:
        lines.append(f"关于「{question}」，我在公司知识库中找到这些依据：")
    lines.append("")
    for i, hit in enumerate(hits, start=1):
        lines.append(f"{i}. 来源：《{hit['title']}》")
        lines.append(hit["content"].strip())
        lines.append("")
    lines.append("说明：以上内容仅来自你可见的公司资料（RAG 检索结果）。")
    return "\n".join(lines).strip()


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
            content=f"已收到材料：[{kind}] {label}",
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

    db.add(ChatMessage(session_id=session.id, role="user", content=message))

    # Governance: list / approve / reject via chat for managers & boss
    pending_match = any(k in message for k in ("待审批", "待我审批", "审批列表"))
    decide_match = re.search(r"(批准|同意|驳回|拒绝)\s*#?\s*(\d+)", message)
    if pending_match or decide_match:
        if decide_match:
            decision = "approve" if decide_match.group(1) in {"批准", "同意"} else "reject"
            req_id = int(decide_match.group(2))
            try:
                req = decide_change_request(db, user=user, request_id=req_id, decision=decision)
                answer = (
                    f"已处理审批单 #{req.id}：{decision}。\n"
                    f"文件：《{req.title}》 action={req.action} status={req.status}"
                )
            except (PermissionError, ValueError) as exc:
                answer = f"无法处理审批：{exc}"
            citations = []
            action = "chat.governance_decide"
        else:
            rows = list_pending_for_approver(db, user)
            if not rows:
                answer = "当前没有待你审批的文件变更。"
            else:
                lines = ["待你审批的重要文件变更："]
                for r in rows:
                    lines.append(
                        f"- #{r.id} [{r.sensitivity}/{r.action}] 《{r.title}》 "
                        f"申请人 {r.requested_by}（回复：批准 #{r.id} / 驳回 #{r.id}）"
                    )
                answer = "\n".join(lines)
            citations = []
            action = "chat.governance_pending"
    elif is_expense_intent(message):
        result = check_expense(db, user=user, session_id=session.id, message=message)
        answer = result["answer"]
        citations = result["citations"]
        action = "chat.expense_check"
    else:
        hits = retrieve(db, user=user, query=message, top_k=5)
        answer = build_answer(message, hits)
        citations = [
            {
                "document_id": h["document_id"],
                "title": h["title"],
                "category": h["category"],
                "snippet": h["content"][:180],
                "score": h["score"],
            }
            for h in hits
        ]
        action = "chat.rag"

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
        detail={"question": message, "hit_count": len(citations)},
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
