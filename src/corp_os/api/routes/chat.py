from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from corp_os.db import get_db
from corp_os.models.iam import User
from corp_os.rag.chat import attach_to_session, chat_with_rag, list_messages
from corp_os.schemas import CitationOut
from corp_os.services.auth import get_current_user
from corp_os.services.expense_check import normalize_kind
from corp_os.services.library import upload_to_library

router = APIRouter()


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


class ChatOut(BaseModel):
    session_id: int
    answer: str
    citations: list[CitationOut] = Field(default_factory=list)


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[CitationOut] = Field(default_factory=list)
    created_at: str | None = None


class ChatUploadOut(BaseModel):
    session_id: int
    document_id: int
    title: str
    kind: str
    tip: str
    needs_approval: bool = False
    request_id: int | None = None
    status: str = "active"
    sensitivity: str = "personal"


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
    resolved_kind = normalize_kind(kind, file.filename or "", note or "", text_override or "")

    category = "other"
    if resolved_kind in {"invoice", "train_ticket", "travel_approval", "itinerary"}:
        category = "invoice" if resolved_kind == "invoice" else "other"
        visibility = "private"
    else:
        lowered = f"{title} {text_override or ''}"
        if any(k in lowered for k in ("制度", "章程", "办法")):
            category = "policy"
            visibility = "company"
        elif any(k in lowered for k in ("通知", "公告")):
            category = "notice"
            visibility = "company"
        elif any(k in lowered for k in ("薪资", "工资")):
            category = "hr"
            visibility = "role"
        elif any(k in lowered for k in ("财务报表", "财报")):
            category = "other"
            visibility = "department"

    visibility_target = None
    if visibility == "role" and any(k in title for k in ("薪资", "工资")):
        visibility_target = "finance"
    if visibility == "department" and any(k in f"{title}{text_override or ''}" for k in ("财务报表", "财报")):
        visibility_target = "finance"

    try:
        doc, meta = upload_to_library(
            db,
            user=user,
            file=file,
            category=category,
            title=title,
            visibility=visibility,
            visibility_target=visibility_target,
            text_override=text_override,
            kind=resolved_kind,
        )
        # attach personal expense materials to chat even if pending (pending company docs still attach note)
        session = attach_to_session(
            db,
            user=user,
            session_id=session_id,
            document_id=doc.id,
            kind=resolved_kind,
            label=title,
        )
        db.commit()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if meta["needs_approval"]:
        tip = (
            f"已提交审批（单号 #{meta['request_id']}，敏感级别 {meta['sensitivity']}）。"
            "重要/机密文件需老板或部门主管同意后才会进入知识库检索。"
        )
    else:
        tip = "材料已加入本对话。你可以直接问：这些够不够报销？还缺什么？"
        if resolved_kind == "other":
            tip = "已上传。若这是报销材料，建议选择类型（发票/车票/审批单）。"

    return ChatUploadOut(
        session_id=session.id,
        document_id=doc.id,
        title=doc.title,
        kind=resolved_kind,
        tip=tip,
        needs_approval=bool(meta["needs_approval"]),
        request_id=meta.get("request_id"),
        status=str(meta.get("status") or doc.status),
        sensitivity=str(meta.get("sensitivity") or "personal"),
    )
