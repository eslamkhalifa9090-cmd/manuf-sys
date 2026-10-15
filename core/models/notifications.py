from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class Notification(Base):
    """Messages to a user, e.g. a document of theirs was returned for correction."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    doc_type: Mapped[str | None] = mapped_column(String(40))
    doc_id: Mapped[int | None] = mapped_column(Integer)
    doc_no: Mapped[str | None] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(120))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
