"""Download / share company knowledge files the user is allowed to view."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.services.permissions import can_view_document
from corp_os.services.session_files import find_library_pptx, resolve_pptx_template

_MEDIA = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".csv": "text/csv; charset=utf-8",
}


def download_url_for(document_id: int) -> str:
    return f"/api/v1/chat/library/{document_id}"


def resolve_library_file(
    db: Session,
    *,
    user: User,
    document_id: int,
) -> tuple[Path, str, str]:
    """Return (path, filename, media_type). Raises ValueError/PermissionError."""
    doc = db.get(Document, document_id)
    if not doc:
        raise ValueError("文件不存在")
    if doc.status not in {"active", "session_temp"}:
        raise ValueError(f"文件当前状态不可下载（{doc.status}）")
    if doc.status == "session_temp" and doc.uploaded_by != user.username:
        from corp_os.services.permissions import is_elevated

        if not is_elevated(user):
            raise PermissionError("无权下载他人的会话暂存文件")
    if not can_view_document(user, doc):
        raise PermissionError("无权下载该文件")
    path = Path(doc.stored_path or "")
    if not path.is_file():
        raise ValueError("文件已丢失或尚未落盘")
    filename = doc.filename or path.name or f"document-{doc.id}"
    ext = Path(filename).suffix.lower() or path.suffix.lower()
    media = _MEDIA.get(ext, doc.content_type or "application/octet-stream")
    return path, filename, media


def share_document_download(
    db: Session,
    *,
    user: User,
    session_id: int,
    document_id: int | None = None,
    query: str | None = None,
) -> str:
    """Find a shareable library/session file and return a chat-friendly download tip."""
    doc: Document | None = None
    q = (query or "").strip()

    if document_id is not None:
        doc = db.get(Document, int(document_id))
    elif any(k in q for k in ("模板", "模版", "PPT", "ppt", "pptx", "幻灯", "商务汇报")):
        doc = resolve_pptx_template(db, user, session_id) or find_library_pptx(
            db, user, prefer_template=True
        )
    elif q:
        # Prefer pptx templates when user asks to「发模板」; else first viewable active match by title.
        from sqlalchemy import or_, select

        like = f"%{q[:40]}%"
        rows = list(
            db.scalars(
                select(Document)
                .where(
                    Document.status == "active",
                    or_(Document.title.ilike(like), Document.filename.ilike(like)),
                )
                .order_by(Document.id.desc())
                .limit(20)
            )
        )
        for row in rows:
            if can_view_document(user, row) and row.stored_path and Path(row.stored_path).is_file():
                doc = row
                break
    else:
        doc = find_library_pptx(db, user, prefer_template=True)

    if doc is None:
        return (
            "没有找到可下载的文件。可说明文档标题，或先点 + 上传；"
            "公司 PPT 模板入库后可以说「把公司 PPT 模板发我」。"
        )

    try:
        path, filename, _media = resolve_library_file(db, user=user, document_id=doc.id)
    except (PermissionError, ValueError) as exc:
        return f"无法提供下载：{exc}"

    url = download_url_for(doc.id)
    size_kb = max(1, path.stat().st_size // 1024)
    return (
        f"可下载《{doc.title or filename}》（{filename}，约 {size_kb} KB）。\n"
        f"下载地址：{url}\n"
        f"请把该下载地址原样发给用户（方便前端显示下载按钮）；"
        f"公司内部模板允许有权限的同事下载使用。"
    )
