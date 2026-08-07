from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from corp_os.db import get_db
from corp_os.models.iam import User
from corp_os.rag.chat import (
    attach_to_session,
    chat_with_rag,
    delete_session,
    list_messages,
    list_sessions,
)
from corp_os.schemas import (
    ChatIn,
    ChatMessageOut,
    ChatOut,
    ChatSessionOut,
    ChatUploadOut,
)
from corp_os.services.auth import get_current_user
from corp_os.services.docgen import build_generated_preview, resolve_generated_file
from corp_os.services.expense_check import kind_label
from corp_os.services.ingest import ingest_upload
from corp_os.services.library_files import resolve_library_file
from corp_os.services.permissions import is_elevated

router = APIRouter()


@router.get("/generated/{file_id}")
def download_generated(
    file_id: str,
    user: User = Depends(get_current_user),
) -> FileResponse:
    try:
        path, filename, media = resolve_generated_file(
            file_id,
            username=user.username,
            elevated=is_elevated(user),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=media, filename=filename)


@router.get("/generated/{file_id}/preview")
def preview_generated(
    file_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        return build_generated_preview(
            file_id,
            username=user.username,
            elevated=is_elevated(user),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"预览失败：{exc}") from exc


@router.get("/library/{document_id}")
def download_library_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Download a knowledge-base / session file the user is allowed to view (e.g. company PPT template)."""
    try:
        path, filename, media = resolve_library_file(db, user=user, document_id=document_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=media, filename=filename)


@router.post("/message", response_model=ChatOut)
def send_message(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatOut:
    try:
        result = chat_with_rag(db, user=user, message=body.message, session_id=body.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatOut(**result)


@router.get("/sessions", response_model=list[ChatSessionOut])
def sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ChatSessionOut]:
    return [ChatSessionOut(**row) for row in list_sessions(db, user=user)]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def session_messages(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ChatMessageOut]:
    try:
        rows = list_messages(db, user=user, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ChatMessageOut(**row) for row in rows]


@router.delete("/sessions/{session_id}")
def remove_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        delete_session(db, user=user, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "session_id": session_id}


@router.post("/upload", response_model=ChatUploadOut)
async def chat_upload(
    file: UploadFile = File(...),
    note: str | None = Form(None),
    kind: str | None = Form(None),
    session_id: int | None = Form(None),
    visibility: str = Form("private"),
    text_override: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatUploadOut:
    title = (note or "").strip() or (file.filename or "未命名资料")
    hint_kind = (kind or "").strip() or None
    if hint_kind in {"", "auto"}:
        hint_kind = None

    try:
        result = ingest_upload(
            db,
            user=user,
            file=file,
            title=title,
            visibility=visibility,
            text_override=text_override,
            kind=hint_kind,
        )
        doc = result.document
        session = attach_to_session(
            db,
            user=user,
            session_id=session_id,
            document_id=doc.id,
            kind=result.kind,
            label=title,
        )
        db.commit()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    label = kind_label(result.kind)
    tip = (
        f"已收到文件《{doc.title}》（识别为「{label}」），目前只暂存在本对话，"
        "还没有写入公司知识库。\n"
        "请告诉我你想怎么用：\n"
        "1）根据这个文件提问 / 帮我看看（只在本对话用）\n"
        "2）上传到知识库（对应主管/老板确认后才会入库检索）\n"
        "3）报销材料：直接问「够不够报销」；类型不对说「这是车票/发票」"
    )

    return ChatUploadOut(
        session_id=session.id,
        document_id=doc.id,
        title=doc.title,
        kind=result.kind,
        tip=tip,
        needs_approval=result.needs_approval,
        request_id=result.request_id,
        status=result.status,
        sensitivity=result.sensitivity,
        session_only=result.session_only,
    )
