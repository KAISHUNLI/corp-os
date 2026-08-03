from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from corp_os.db import get_db
from corp_os.models.iam import User
from corp_os.services.auth import get_current_user
from corp_os.services.governance import decide_change_request, list_pending_for_approver
from corp_os.services.library import request_document_delete

router = APIRouter()


class ChangeRequestOut(BaseModel):
    id: int
    action: str
    document_id: int | None
    status: str
    sensitivity: str
    title: str
    category: str
    visibility: str
    department_code: str | None
    requested_by: str
    reason: str | None = None

    model_config = {"from_attributes": True}


class DecideIn(BaseModel):
    decision: str = Field(description="approve | reject")
    note: str | None = None


class DeleteIn(BaseModel):
    reason: str | None = None


@router.get("/pending", response_model=list[ChangeRequestOut])
def pending_requests(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list:
    return list_pending_for_approver(db, user)


@router.post("/requests/{request_id}/decide", response_model=ChangeRequestOut)
def decide(
    request_id: int,
    body: DecideIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChangeRequestOut:
    try:
        req = decide_change_request(
            db, user=user, request_id=request_id, decision=body.decision, note=body.note
        )
        db.commit()
        db.refresh(req)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChangeRequestOut.model_validate(req)


@router.post("/documents/{document_id}/delete")
def delete_document(
    document_id: int,
    body: DeleteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        result = request_document_delete(db, user=user, document_id=document_id, reason=body.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
