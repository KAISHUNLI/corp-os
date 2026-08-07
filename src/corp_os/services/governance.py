"""Upload permission + approval governance for important documents."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.domain.categories import category_owner_hint, owner_departments_for
from corp_os.models.document import Document
from corp_os.models.governance import DocumentChangeRequest
from corp_os.models.iam import User
from corp_os.rag.store import index_document
from corp_os.services.audit import write_audit
from corp_os.services.permissions import is_elevated


# Categories / visibility that always need boss or dept manager approval.
IMPORTANT_CATEGORIES = {"policy", "notice", "hr", "contract", "tech"}
CRITICAL_HINTS = ("薪资", "工资", "财务报表", "财报", "机密", "secret", "salary")


def classify_sensitivity(
    *,
    category: str,
    visibility: str,
    title: str = "",
    text: str = "",
    kind: str | None = None,
) -> str:
    """personal | important | critical"""
    blob = f"{title} {text} {kind or ''}"
    if any(k in blob for k in CRITICAL_HINTS) or (
        category in {"hr", "other"} and visibility in {"role", "department"} and any(k in blob for k in ("薪资", "财务", "报表"))
    ):
        return "critical"
    if kind in {"invoice", "train_ticket", "travel_approval", "itinerary"} and visibility == "private":
        return "personal"
    if visibility == "private" and category in {"invoice", "other"}:
        return "personal"
    if category in IMPORTANT_CATEGORIES or visibility in {"company", "department", "role"}:
        return "important"
    return "personal"


def can_upload_company_knowledge(user: User) -> bool:
    """公司知识（制度/通知/部门资料等）仅主管、老板、管理员可传。"""
    return is_elevated(user) or bool(user.is_dept_manager)


def is_session_ephemeral_uploader(user: User) -> bool:
    """普通员工：上传仅供当前对话（类似豆包），不进公司档案/向量库。"""
    return not can_upload_company_knowledge(user)


def can_own_category(user: User, *, category: str, visibility: str) -> bool:
    """对应主管：行政制度归人事、技术资料归交付等；老板/管理员全类目。"""
    if is_elevated(user):
        return True
    if not user.is_dept_manager:
        return False
    dept = (user.department_code or "").strip()
    owners = owner_departments_for(category)
    if owners:
        return dept in owners
    # other：本部门主管只能传本部门可见，不能冒充全公司规范
    if visibility == "department":
        return bool(dept)
    if visibility == "private" and category in {"invoice", "other"}:
        return True
    return False


def can_upload(user: User, *, category: str, visibility: str, sensitivity: str) -> tuple[bool, str]:
    """员工=会话临时材料；公司知识=对应类目主管/老板。"""
    if is_elevated(user):
        return True, "ok"

    is_personal_private = visibility == "private" and sensitivity == "personal"
    if is_session_ephemeral_uploader(user):
        if is_personal_private:
            return True, "ok"
        return (
            False,
            "普通员工上传仅用于当前对话（报销预审等），不能写入公司知识库；公司资料请由对应主管或老板上传",
        )

    if not can_own_category(user, category=category, visibility=visibility):
        hint = category_owner_hint(category)
        return False, f"该类公司知识（{category}）仅可由{hint}上传"

    return True, "ok"


def needs_approval(user: User, sensitivity: str) -> bool:
    if is_elevated(user):
        return False  # boss/admin can publish directly
    return sensitivity in {"important", "critical"}


def can_approve_request(user: User, req: DocumentChangeRequest) -> bool:
    if is_elevated(user):
        return True
    if user.is_dept_manager and user.department_code and req.department_code == user.department_code:
        # dept manager cannot alone approve critical — need boss; for important ok
        if req.sensitivity == "critical":
            return False
        return True
    return False


def create_change_request(
    db: Session,
    *,
    user: User,
    action: str,
    document: Document | None,
    sensitivity: str,
    reason: str | None = None,
    payload: dict | None = None,
) -> DocumentChangeRequest:
    doc = document
    req = DocumentChangeRequest(
        action=action,
        document_id=doc.id if doc else None,
        status="pending",
        sensitivity=sensitivity,
        title=(doc.title if doc else (payload or {}).get("title", ""))[:256],
        category=(doc.category if doc else (payload or {}).get("category", "other")),
        visibility=(doc.visibility if doc else (payload or {}).get("visibility", "company")),
        department_code=(doc.department_code if doc else user.department_code),
        requested_by=user.username,
        reason=reason,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(req)
    db.flush()
    write_audit(
        db,
        actor=user.username,
        action=f"governance.{action}.submit",
        resource_type="change_request",
        resource_id=str(req.id),
        detail={"sensitivity": sensitivity, "document_id": req.document_id, "title": req.title},
    )
    return req


def list_pending_for_approver(db: Session, user: User) -> list[DocumentChangeRequest]:
    rows = list(
        db.scalars(
            select(DocumentChangeRequest)
            .where(DocumentChangeRequest.status == "pending")
            .order_by(DocumentChangeRequest.id.desc())
        )
    )
    return [r for r in rows if can_approve_request(user, r)]


def decide_change_request(
    db: Session,
    *,
    user: User,
    request_id: int,
    decision: str,
    note: str | None = None,
) -> DocumentChangeRequest:
    req = db.get(DocumentChangeRequest, request_id)
    if not req or req.status != "pending":
        raise ValueError("审批单不存在或已处理")
    if not can_approve_request(user, req):
        raise PermissionError("你无权审批该申请（机密类需老板；部门类需对应主管或老板）")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision 必须是 approve 或 reject")

    req.decided_by = user.username
    req.decision_note = note
    req.decided_at = datetime.now(timezone.utc)

    doc = db.get(Document, req.document_id) if req.document_id else None

    if decision == "reject":
        req.status = "rejected"
        if doc and req.action == "create":
            doc.status = "rejected"
        elif doc and req.action == "delete":
            doc.status = "active"  # cancel delete
        write_audit(
            db,
            actor=user.username,
            action="governance.reject",
            resource_type="change_request",
            resource_id=str(req.id),
            detail={"note": note},
        )
        db.flush()
        return req

    # approve
    req.status = "approved"
    if req.action == "create" and doc:
        doc.status = "active"
        index_document(db, doc)
    elif req.action == "delete" and doc:
        doc.status = "archived"
        # remove from RAG by re-index empty / delete chunks via index with empty
        doc.full_text = None
        doc.text_excerpt = None
        index_document(db, doc)
    elif req.action == "update" and doc:
        doc.status = "active"
        index_document(db, doc)

    write_audit(
        db,
        actor=user.username,
        action="governance.approve",
        resource_type="change_request",
        resource_id=str(req.id),
        detail={"document_id": req.document_id, "action": req.action},
    )
    db.flush()
    return req
