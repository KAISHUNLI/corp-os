from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.domain.categories import CATEGORY_MAP
from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.rag.store import index_document
from corp_os.services.audit import write_audit
from corp_os.services.governance import (
    can_upload,
    classify_sensitivity,
    create_change_request,
    needs_approval,
)


ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".txt", ".md", ".xlsx", ".csv"}


def _read_text(path: Path, override: str | None) -> str:
    if override and override.strip():
        return override.strip()
    if path.suffix.lower() in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def upload_to_library(
    db: Session,
    *,
    user: User,
    file: UploadFile,
    category: str,
    title: str | None,
    visibility: str,
    visibility_target: str | None,
    text_override: str | None = None,
    kind: str | None = None,
) -> tuple[Document, dict]:
    """Upload a document. Important/critical ones stay pending until approved.

    Returns (document, meta) where meta includes needs_approval / request_id / sensitivity.
    """
    if category not in CATEGORY_MAP:
        raise ValueError(f"未知分类: {category}")
    if visibility not in {"company", "department", "role", "private"}:
        raise ValueError("visibility 必须是 company/department/role/private")

    ext = Path(file.filename or "upload.bin").suffix.lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {ext}")

    if visibility == "department" and not (visibility_target or user.department_code):
        raise ValueError("部门可见需要指定部门或当前用户已有部门")
    if visibility == "role" and not visibility_target:
        raise ValueError("角色可见需要指定 role code")

    display_title = (title or "").strip() or (file.filename or "upload")
    # peek text for sensitivity before saving fully - we'll read after save
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stored = settings.upload_dir / f"{uuid.uuid4().hex}{ext or '.bin'}"
    with stored.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    text = _read_text(stored, text_override)
    sensitivity = classify_sensitivity(
        category=category,
        visibility=visibility,
        title=display_title,
        text=text,
        kind=kind,
    )
    ok, reason = can_upload(user, category=category, visibility=visibility, sensitivity=sensitivity)
    if not ok:
        stored.unlink(missing_ok=True)
        raise PermissionError(reason)

    target = visibility_target
    if visibility == "department" and not target:
        target = user.department_code

    pending = needs_approval(user, sensitivity)
    doc = Document(
        title=display_title[:256],
        filename=file.filename or stored.name,
        stored_path=str(stored),
        content_type=file.content_type,
        size_bytes=stored.stat().st_size,
        category=category,
        doc_type=category,
        visibility=visibility,
        visibility_target=target,
        status="pending_approval" if pending else "active",
        uploaded_by=user.username,
        department_code=user.department_code,
        text_excerpt=(text[:4000] if text else None),
        full_text=(text if text else None),
    )
    db.add(doc)
    db.flush()

    request_id = None
    if pending:
        req = create_change_request(
            db,
            user=user,
            action="create",
            document=doc,
            sensitivity=sensitivity,
            reason="重要/机密文件新增需主管或老板审批后才能入库检索",
            payload={"kind": kind},
        )
        request_id = req.id
    else:
        index_document(db, doc)

    write_audit(
        db,
        actor=user.username,
        action="library.upload",
        resource_type="document",
        resource_id=str(doc.id),
        detail={
            "category": category,
            "visibility": visibility,
            "visibility_target": target,
            "title": doc.title,
            "sensitivity": sensitivity,
            "needs_approval": pending,
            "request_id": request_id,
        },
    )
    db.commit()
    db.refresh(doc)
    return doc, {
        "needs_approval": pending,
        "request_id": request_id,
        "sensitivity": sensitivity,
        "status": doc.status,
    }


def request_document_delete(db: Session, *, user: User, document_id: int, reason: str | None = None) -> dict:
    doc = db.get(Document, document_id)
    if not doc or doc.status not in {"active", "pending_approval"}:
        raise ValueError("文档不存在")
    if not (doc.uploaded_by == user.username or user.is_dept_manager or user.role_code in {"admin", "boss"}):
        raise PermissionError("无权申请删除该文档")

    sensitivity = classify_sensitivity(
        category=doc.category,
        visibility=doc.visibility,
        title=doc.title,
        text=doc.full_text or "",
    )
    if not needs_approval(user, sensitivity) and doc.uploaded_by == user.username and sensitivity == "personal":
        doc.status = "archived"
        index_document(db, doc)
        db.commit()
        return {"needs_approval": False, "status": "archived", "request_id": None}

    doc.status = "pending_delete"
    req = create_change_request(
        db,
        user=user,
        action="delete",
        document=doc,
        sensitivity=sensitivity if sensitivity != "personal" else "important",
        reason=reason or "申请删除重要文件",
    )
    db.commit()
    return {"needs_approval": True, "status": "pending_delete", "request_id": req.id}
