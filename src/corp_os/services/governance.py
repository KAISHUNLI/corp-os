"""Upload permission + approval governance for important documents."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def can_upload(user: User, *, category: str, visibility: str, sensitivity: str) -> tuple[bool, str]:
    """Whether user may submit this upload (possibly into approval queue)."""
    if is_elevated(user):
        return True, "ok"
    if sensitivity == "personal":
        return True, "ok"
    # Important/critical: only related roles may propose
    if sensitivity == "critical":
        if user.role_code in {"finance", "hr"} or user.is_dept_manager:
            return True, "ok"
        return False, "机密类文件（薪资/财报等）仅财务/人事或部门主管可提交，且需老板或主管审批"
    # important
    if user.role_code in {"employee", "legal", "finance"} or user.is_dept_manager:
        # employees can propose department/company docs but need approval
        if visibility == "company" and user.role_code == "employee" and not user.is_dept_manager:
            return True, "ok"  # queued for approval
        return True, "ok"
    return False, "当前角色无权上传该类文件"


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
