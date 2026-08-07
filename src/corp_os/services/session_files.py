"""Session-scoped files: stage first, publish to knowledge library only on confirm."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.models.rag import ChatAttachment
from corp_os.rag.store import index_document
from corp_os.services.audit import write_audit
from corp_os.services.governance import (
    can_upload,
    can_upload_company_knowledge,
    classify_sensitivity,
    create_change_request,
    needs_approval,
)
from corp_os.services.permissions import is_elevated

_LIBRARY_PUBLISH_RE = re.compile(
    r"(上传到?知识库|写入知识库|进(入)?知识库|发布到知识库|正式上传|"
    r"归档到知识库|确认上传|这是要上传|入库到知识库|知识库入库|"
    r"放到知识库|存进知识库|作为(公司)?制度上传)"
)


def is_library_publish_intent(text: str) -> bool:
    return bool(_LIBRARY_PUBLISH_RE.search((text or "").strip()))


def list_session_documents(db: Session, session_id: int) -> list[tuple[ChatAttachment, Document]]:
    rows = list(
        db.scalars(
            select(ChatAttachment)
            .where(ChatAttachment.session_id == session_id)
            .order_by(ChatAttachment.id.asc())
        )
    )
    out: list[tuple[ChatAttachment, Document]] = []
    for att in rows:
        doc = db.get(Document, att.document_id)
        if doc:
            out.append((att, doc))
    return out


def list_staged_documents(db: Session, session_id: int) -> list[tuple[ChatAttachment, Document]]:
    return [
        (att, doc)
        for att, doc in list_session_documents(db, session_id)
        if doc.status == "session_temp"
    ]


def find_session_pptx(
    db: Session,
    session_id: int,
    *,
    document_id: int | None = None,
) -> Document | None:
    """Pick a PPTX from this session (prefer names containing 模板/模版)."""
    from pathlib import Path

    candidates: list[Document] = []
    for _att, doc in list_session_documents(db, session_id):
        name = (doc.filename or "").lower()
        if not name.endswith(".pptx"):
            continue
        if not doc.stored_path or not Path(doc.stored_path).is_file():
            continue
        if document_id is not None and doc.id != document_id:
            continue
        candidates.append(doc)
    if document_id is not None:
        return candidates[0] if candidates else None
    if not candidates:
        return None

    def score(doc: Document) -> tuple[int, int]:
        blob = f"{doc.title or ''} {doc.filename or ''}"
        prefer = 1 if any(k in blob for k in ("模板", "模版", "template", "汇报")) else 0
        return (prefer, doc.id)

    return sorted(candidates, key=score)[-1]


def find_library_pptx(
    db: Session,
    user: User,
    *,
    document_id: int | None = None,
    prefer_template: bool = True,
) -> Document | None:
    """Find a PPTX the user can view in knowledge base (company templates etc.)."""
    from pathlib import Path

    from corp_os.services.permissions import can_view_document

    if document_id is not None:
        doc = db.get(Document, document_id)
        if not doc:
            return None
        name = (doc.filename or "").lower()
        if not name.endswith(".pptx"):
            return None
        if doc.status not in {"active", "session_temp"}:
            return None
        if not doc.stored_path or not Path(doc.stored_path).is_file():
            return None
        if not can_view_document(user, doc):
            return None
        return doc

    rows = list(
        db.scalars(
            select(Document)
            .where(Document.status == "active")
            .order_by(Document.id.desc())
            .limit(80)
        )
    )
    candidates: list[Document] = []
    for doc in rows:
        name = (doc.filename or "").lower()
        if not name.endswith(".pptx"):
            continue
        if not doc.stored_path or not Path(doc.stored_path).is_file():
            continue
        if not can_view_document(user, doc):
            continue
        candidates.append(doc)
    if not candidates:
        return None

    def score(doc: Document) -> tuple[int, int, int]:
        blob = f"{doc.title or ''} {doc.filename or ''}"
        tpl = 1 if any(k in blob for k in ("模板", "模版", "template")) else 0
        report = 1 if any(k in blob for k in ("汇报", "商务", "report")) else 0
        if not prefer_template:
            tpl = 0
        return (tpl, report, doc.id)

    return sorted(candidates, key=score)[-1]


def resolve_pptx_template(
    db: Session,
    user: User,
    session_id: int,
    *,
    document_id: int | None = None,
) -> Document | None:
    """Session attachment first, then company library PPTX templates."""
    if document_id is not None:
        # Explicit id: try session, then library.
        hit = find_session_pptx(db, session_id, document_id=document_id)
        if hit is not None:
            return hit
        return find_library_pptx(db, user, document_id=document_id)

    hit = find_session_pptx(db, session_id)
    if hit is not None:
        return hit
    return find_library_pptx(db, user, prefer_template=True)


def session_file_hits(db: Session, session_id: int, *, max_chars: int = 6000) -> list[dict]:
    """Turn current-session staged/attached files into RAG-style context hits."""
    hits: list[dict] = []
    remaining = max_chars
    for att, doc in list_session_documents(db, session_id):
        text = (doc.full_text or doc.text_excerpt or "").strip()
        if not text:
            continue
        snippet = text[: min(len(text), remaining, 2500)]
        hits.append(
            {
                "score": 1.0,
                "chunk_id": 0,
                "document_id": doc.id,
                "title": f"本会话文件·{doc.title or att.label}",
                "category": "session_file",
                "content": snippet,
            }
        )
        remaining -= len(snippet)
        if remaining <= 200:
            break
    return hits


def _promote_for_library(doc: Document, user: User) -> str | None:
    """If staged as personal/private, promote visibility when publisher is boss/manager.

    Returns None if ok to continue, or an error message for employees.
    """
    sensitivity = classify_sensitivity(
        category=doc.category,
        visibility=doc.visibility,
        title=doc.title or "",
        text=doc.text_excerpt or doc.full_text or "",
    )
    if not (doc.visibility == "private" and sensitivity == "personal"):
        return None

    if is_elevated(user):
        doc.visibility = "company"
        doc.visibility_target = None
        # Office templates / reports staged as other → treat as company knowledge category.
        name = f"{doc.filename or ''} {doc.title or ''}".lower()
        if doc.category == "other":
            if name.endswith(".pptx") or "汇报" in name or "模板" in name or "ppt" in name:
                doc.category = "tech"
            elif any(k in name for k in ("制度", "办法", "章程", "手册")):
                doc.category = "policy"
        return None

    if can_upload_company_knowledge(user) and user.department_code:
        doc.visibility = "department"
        doc.visibility_target = user.department_code
        return None

    return (
        f"《{doc.title}》：识别为个人材料，默认只留在本对话。"
        "公司知识入库请由对应主管或老板操作，或先说明类目/可见范围后再上传。"
    )


def publish_staged_to_library(
    db: Session,
    *,
    user: User,
    session_id: int,
    document_id: int | None = None,
) -> str:
    """Promote session_temp doc(s) into company knowledge (approval / index)."""
    staged = list_staged_documents(db, session_id)
    if document_id is not None:
        staged = [(a, d) for a, d in staged if d.id == document_id]
    if not staged:
        return (
            "当前对话没有待确认的暂存文件。"
            "请先点 + 发送文件；确认要进公司知识库时再说「上传到知识库」。"
        )

    lines: list[str] = []
    for _att, doc in staged:
        if not (doc.uploaded_by == user.username or is_elevated(user)):
            lines.append(f"《{doc.title}》：无权处理他人文件")
            continue

        refuse = _promote_for_library(doc, user)
        if refuse:
            lines.append(refuse)
            continue

        sensitivity = classify_sensitivity(
            category=doc.category,
            visibility=doc.visibility,
            title=doc.title or "",
            text=doc.text_excerpt or doc.full_text or "",
        )
        ok, reason = can_upload(
            user,
            category=doc.category,
            visibility=doc.visibility,
            sensitivity=sensitivity,
        )
        if not ok:
            lines.append(f"《{doc.title}》：不能写入公司知识库 — {reason}")
            continue

        pending = needs_approval(user, sensitivity)
        if pending:
            doc.status = "pending_approval"
            req = create_change_request(
                db,
                user=user,
                action="create",
                document=doc,
                sensitivity=sensitivity,
                reason="用户确认：将暂存文件写入公司知识库",
            )
            lines.append(
                f"《{doc.title}》：已提交知识库入库审批（单号 #{req.id}，{sensitivity}）。"
                "批准后才会进入检索。"
            )
        else:
            doc.status = "active"
            index_document(db, doc)
            scope = doc.visibility
            if doc.visibility_target:
                scope = f"{doc.visibility}:{doc.visibility_target}"
            lines.append(
                f"《{doc.title}》：已写入公司知识库（可见范围 {scope}，类目 {doc.category}），可被检索。"
            )

        write_audit(
            db,
            actor=user.username,
            action="library.publish_from_session",
            resource_type="document",
            resource_id=str(doc.id),
            detail={
                "sensitivity": sensitivity,
                "pending": pending,
                "session_id": session_id,
                "visibility": doc.visibility,
                "category": doc.category,
            },
        )

    db.flush()
    return "\n".join(lines) if lines else "没有可处理的暂存文件。"
