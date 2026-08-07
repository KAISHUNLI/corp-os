"""Upload line (线1): gate → classify → authorize → commit.

Do not embed/index until authorize says the doc may go live.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.domain.categories import CATEGORY_MAP
from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.rag.store import index_document
from corp_os.services.audit import write_audit
from corp_os.services.expense_check import normalize_kind
from corp_os.services.extract import SUPPORTED_EXTENSIONS, extract_text
from corp_os.services.governance import (
    can_upload,
    classify_sensitivity,
    create_change_request,
    is_session_ephemeral_uploader,
    needs_approval,
)

# Step ① hard limits（病毒扫描仍待补）


@dataclass
class IngestResult:
    document: Document
    needs_approval: bool
    request_id: int | None
    sensitivity: str
    status: str
    kind: str
    session_only: bool = False


@dataclass
class _GateOk:
    stored: Path
    filename: str
    content_type: str | None
    size_bytes: int


def _safe_filename(name: str) -> str:
    base = Path(name).name.replace("\x00", "")
    if not base or base in {".", ".."}:
        return "upload.bin"
    if ".." in base or "/" in base or "\\" in base:
        raise ValueError("非法文件名")
    # block double extensions like invoice.pdf.exe
    suffixes = Path(base).suffixes
    dangerous = {
        ".exe",
        ".bat",
        ".cmd",
        ".js",
        ".msi",
        ".sh",
        ".dll",
        ".com",
        ".scr",
        ".ps1",
        ".vbs",
        ".jar",
    }
    if len(suffixes) >= 2 and suffixes[-1].lower() in dangerous:
        raise ValueError(f"危险文件类型: {suffixes[-1]}")
    return base


def infer_taxonomy(
    *,
    title: str,
    filename: str,
    text: str = "",
    kind_hint: str | None = None,
    visibility_default: str = "private",
) -> tuple[str, str, str | None, str]:
    """Guess category / visibility / kind from filename + text."""
    preview_kind = normalize_kind(kind_hint, filename, title, text)
    blob = f"{title} {filename} {text}"
    ext = Path(filename or "").suffix.lower()
    category = "other"
    visibility = visibility_default
    visibility_target: str | None = None

    if preview_kind in {"invoice", "train_ticket", "travel_approval", "itinerary"}:
        category = "invoice" if preview_kind == "invoice" else "other"
        visibility = "private"
    elif preview_kind == "policy" or any(k in blob for k in ("制度", "章程", "办法")):
        category = "policy"
        visibility = "company"
    elif preview_kind == "notice" or any(k in blob for k in ("通知", "公告")):
        category = "notice"
        visibility = "company"
    elif any(k in blob for k in ("薪资", "工资")):
        category = "hr"
        visibility = "role"
        visibility_target = "finance"
    elif any(k in blob for k in ("财务报表", "财报")):
        category = "other"
        visibility = "department"
        visibility_target = "finance"
    elif ext == ".pptx" or any(k in blob for k in ("汇报", "路演", "演讲稿", "演示文稿", "模板")):
        # PPT / 汇报材料：先按技术资料归类；入库时由主管/老板再定可见范围。
        category = "tech"
        # 暂存阶段仍可用 private；显式「上传到知识库」时会 promote。

    return category, visibility, visibility_target, preview_kind


def gate_file(file: UploadFile) -> _GateOk:
    """① 准入：类型 / 大小，落盘。失败则不进入后续。"""
    filename = _safe_filename(file.filename or "upload.bin")
    ext = Path(filename).suffix.lower()
    if ext and ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {ext}")

    settings = get_settings()
    max_bytes = int(settings.max_upload_bytes or (20 * 1024 * 1024))
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stored = settings.upload_dir / f"{uuid.uuid4().hex}{ext or '.bin'}"
    with stored.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    size = stored.stat().st_size
    if size <= 0:
        stored.unlink(missing_ok=True)
        raise ValueError("空文件不能上传")
    if size > max_bytes:
        stored.unlink(missing_ok=True)
        raise ValueError(f"文件过大（上限 {max_bytes // (1024 * 1024)}MB）")

    return _GateOk(
        stored=stored,
        filename=filename,
        content_type=file.content_type,
        size_bytes=size,
    )


def classify_content(
    gate: _GateOk,
    *,
    title: str,
    category: str,
    visibility: str,
    visibility_target: str | None,
    kind_hint: str | None,
    text_override: str | None,
    user: User,
) -> tuple[str, str, str, str | None, str, str]:
    """② 抽文本 + 定 kind / 敏感度。"""
    if category not in CATEGORY_MAP:
        raise ValueError(f"未知分类: {category}")
    if visibility not in {"company", "department", "role", "private"}:
        raise ValueError("visibility 必须是 company/department/role/private")
    if visibility == "department" and not (visibility_target or user.department_code):
        raise ValueError("部门可见需要指定部门或当前用户已有部门")
    if visibility == "role" and not visibility_target:
        raise ValueError("角色可见需要指定 role code")

    text = extract_text(gate.stored, override=text_override)
    kind = kind_hint
    if not kind or kind == "auto":
        kind = normalize_kind(None, gate.filename, title, text)

    # Refine taxonomy once we have body text (e.g. 制度关键词在正文里).
    cat2, vis2, tgt2, kind2 = infer_taxonomy(
        title=title,
        filename=gate.filename,
        text=text,
        kind_hint=kind,
        visibility_default=visibility,
    )
    if category == "other" and cat2 != "other":
        category = cat2
        visibility = vis2
        visibility_target = tgt2 or visibility_target
    if kind in {None, "auto", "other"} and kind2 != "other":
        kind = kind2

    sensitivity = classify_sensitivity(
        category=category,
        visibility=visibility,
        title=title,
        text=text,
        kind=kind,
    )
    return text, category, visibility, visibility_target, kind or "other", sensitivity


def authorize_upload(
    user: User,
    *,
    category: str,
    visibility: str,
    sensitivity: str,
) -> bool:
    """③ 权限：能否提交；返回是否必须审批后才能进检索。"""
    ok, reason = can_upload(user, category=category, visibility=visibility, sensitivity=sensitivity)
    if not ok:
        raise PermissionError(reason)
    if is_session_ephemeral_uploader(user):
        return False  # 会话临时材料：不审批、不入库
    return needs_approval(user, sensitivity)


def commit_document(
    db: Session,
    *,
    user: User,
    gate: _GateOk,
    title: str,
    text: str,
    category: str,
    visibility: str,
    visibility_target: str | None,
    kind: str,
    sensitivity: str,
    pending: bool = False,
    stage_only: bool = True,
) -> IngestResult:
    """④ 落库。默认 stage_only：只挂会话，不进知识库；确认后再 publish。"""
    target = visibility_target
    if visibility == "department" and not target:
        target = user.department_code

    # 发送文件先一律暂存；入库需用户明确「上传到知识库」。
    if stage_only:
        status = "session_temp"
        pending = False
        session_only = True
    elif is_session_ephemeral_uploader(user):
        status = "session_temp"
        visibility = "private"
        target = None
        pending = False
        session_only = True
    elif pending:
        status = "pending_approval"
        session_only = False
    else:
        status = "active"
        session_only = False

    doc = Document(
        title=title[:256],
        filename=gate.filename,
        stored_path=str(gate.stored),
        content_type=gate.content_type,
        size_bytes=gate.size_bytes,
        category=category,
        doc_type=category,
        visibility=visibility,
        visibility_target=target,
        status=status,
        uploaded_by=user.username,
        department_code=user.department_code,
        text_excerpt=(text[:4000] if text else None),
        full_text=(text if text else None),
    )
    db.add(doc)
    db.flush()

    request_id = None
    if session_only or stage_only:
        pass  # 不切片、不审批
    elif pending:
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
        action="chat.stage_file" if stage_only else ("chat.temp_upload" if session_only else "library.upload"),
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
            "kind": kind,
            "session_only": session_only,
            "stage_only": stage_only,
        },
    )
    db.commit()
    db.refresh(doc)
    return IngestResult(
        document=doc,
        needs_approval=pending,
        request_id=request_id,
        sensitivity=sensitivity,
        status=doc.status,
        kind=kind,
        session_only=True if stage_only else session_only,
    )


def ingest_upload(
    db: Session,
    *,
    user: User,
    file: UploadFile,
    title: str | None = None,
    visibility: str = "private",
    text_override: str | None = None,
    kind: str | None = None,
) -> IngestResult:
    """发送文件：gate → classify → 暂存会话。不直接进公司知识库。"""
    display_title = (title or "").strip() or (file.filename or "upload")
    gate = gate_file(file)
    try:
        category, visibility, visibility_target, _ = infer_taxonomy(
            title=display_title,
            filename=file.filename or gate.filename,
            text=text_override or "",
            kind_hint=kind,
            visibility_default=visibility,
        )
        text, category, visibility, visibility_target, resolved_kind, sensitivity = classify_content(
            gate,
            title=display_title,
            category=category,
            visibility=visibility,
            visibility_target=visibility_target,
            kind_hint=kind,
            text_override=text_override,
            user=user,
        )
        # 不在此处 authorize 入库；仅校验「能否把文件放进对话」——任何人可暂存。
        return commit_document(
            db,
            user=user,
            gate=gate,
            title=display_title,
            text=text,
            category=category,
            visibility=visibility,
            visibility_target=visibility_target,
            kind=resolved_kind,
            sensitivity=sensitivity,
            pending=False,
            stage_only=True,
        )
    except Exception:
        gate.stored.unlink(missing_ok=True)
        raise
