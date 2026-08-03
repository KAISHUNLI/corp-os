from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from corp_os.db import Base


class DocumentChangeRequest(Base):
    """Approval workflow for important document create/update/delete."""

    __tablename__ = "document_change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(16), index=True)  # create|update|delete
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)  # pending|approved|rejected
    sensitivity: Mapped[str] = mapped_column(String(32), default="important")  # personal|important|critical
    title: Mapped[str] = mapped_column(String(256), default="")
    category: Mapped[str] = mapped_column(String(64), default="other")
    visibility: Mapped[str] = mapped_column(String(32), default="company")
    department_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requested_by: Mapped[str] = mapped_column(String(64), index=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
