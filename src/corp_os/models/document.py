from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from corp_os.db import Base


class Document(Base):
    """Internal knowledge warehouse document."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    filename: Mapped[str] = mapped_column(String(256))
    stored_path: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    # Taxonomy
    category: Mapped[str] = mapped_column(String(64), index=True, default="other")
    doc_type: Mapped[str] = mapped_column(String(32), default="file")  # legacy/special tag

    # Access control
    # company | department | role | private
    visibility: Mapped[str] = mapped_column(String(32), default="company", index=True)
    visibility_target: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(32), default="active")  # active|rejected|archived
    uploaded_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    department_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Searchable body (scaffold; later chunked embeddings)
    text_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
