"""Document delete / approval helpers. Upload path lives in services.ingest."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.models.document import Document
from corp_os.models.governance import DocumentChangeRequest
from corp_os.models.iam import User
from corp_os.rag.store import index_document
from corp_os.services.audit import write_audit
from corp_os.services.governance import (
    classify_sensitivity,
    create_change_request,
    needs_approval,
)
from corp_os.services.permissions import is_elevated


def _can_manage_document(user: User, doc: Document) -> bool:
    if is_elevated(user):
        return True
    if doc.uploaded_by == user.username:
        return True
    if user.is_dept_manager and user.department_code and doc.department_code == user.department_code:
        return True
    return False


def _purge_from_rag(db: Session, doc: Document) -> None:
    """Archive + drop PG chunks / Milvus vectors so wrong uploads stop being retrieved."""
    doc.status = "archived"
    doc.full_text = None
    doc.text_excerpt = None
    index_document(db, doc)


def request_document_delete(db: Session, *, user: User, document_id: int, reason: str | None = None) -> dict:
    doc = db.get(Document, document_id)
    if not doc or doc.status not in {"active", "pending_approval"}:
        raise ValueError("文档不存在")
    if not _can_manage_document(user, doc):
        raise PermissionError("无权删除该文档")

    # 尚未入库检索：上传人/主管可直接撤回，无需再走一遍审批。
    if doc.status == "pending_approval":
        doc.status = "rejected"
        pending = list(
            db.scalars(
                select(DocumentChangeRequest).where(
                    DocumentChangeRequest.document_id == doc.id,
                    DocumentChangeRequest.status == "pending",
                    DocumentChangeRequest.action == "create",
                )
            )
        )
        for req in pending:
            req.status = "rejected"
            req.decision_note = reason or "上传人撤回（传错/作废）"
        write_audit(
            db,
            actor=user.username,
            action="governance.withdraw",
            resource_type="document",
            resource_id=str(doc.id),
            detail={"reason": reason, "was": "pending_approval"},
        )
        db.commit()
        return {"needs_approval": False, "status": "rejected", "request_id": None, "purged_from_rag": False}

    sensitivity = classify_sensitivity(
        category=doc.category,
        visibility=doc.visibility,
        title=doc.title,
        text=doc.text_excerpt or "",
    )

    # 个人材料或老板/管理员：立刻下架并清向量。
    if is_elevated(user) or (
        not needs_approval(user, sensitivity)
        and doc.uploaded_by == user.username
        and sensitivity == "personal"
    ):
        _purge_from_rag(db, doc)
        write_audit(
            db,
            actor=user.username,
            action="library.delete",
            resource_type="document",
            resource_id=str(doc.id),
            detail={"reason": reason, "immediate": True},
        )
        db.commit()
        return {"needs_approval": False, "status": "archived", "request_id": None, "purged_from_rag": True}

    req = create_change_request(
        db,
        user=user,
        action="delete",
        document=doc,
        sensitivity=sensitivity,
        reason=reason or "请求删除文档（传错/过期/需更正）",
    )
    db.commit()
    return {
        "needs_approval": True,
        "status": "pending_delete",
        "request_id": req.id,
        "purged_from_rag": False,
        "hint": "已提交删除审批；批准后会从知识库下架并清除向量。也可请老板直接删除。",
    }
