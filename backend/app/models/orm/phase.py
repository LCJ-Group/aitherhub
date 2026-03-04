from datetime import datetime

from sqlalchemy import ForeignKey, Text, Integer, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.orm.base import Base, UUIDMixin, TimestampMixin


class Phase(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "phases"

    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id"),
        nullable=False,
    )
    phase_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase_index: Mapped[int] = mapped_column(Integer, nullable=False)
    phase_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    time_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    view_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    like_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    like_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    delta_view: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delta_like: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    sales_psychology_tags: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
