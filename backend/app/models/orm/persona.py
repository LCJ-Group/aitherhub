# app/models/orm/persona.py
from datetime import datetime
from sqlalchemy import ForeignKey, Text, Integer, String, DateTime, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.models.orm.base import Base, UUIDMixin, TimestampMixin


class Persona(Base, UUIDMixin, TimestampMixin):
    """Represents a streamer persona / clone profile."""
    __tablename__ = "personas"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ElevenLabs voice clone
    voice_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    voice_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # OpenAI fine-tuned model
    finetune_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    finetune_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    finetune_status: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="none"
    )  # none | preparing | training | completed | failed

    # Training data stats
    training_video_count: Mapped[int] = mapped_column(Integer, default=0)
    training_segment_count: Mapped[int] = mapped_column(Integer, default=0)
    training_duration_hours: Mapped[float] = mapped_column(Float, default=0.0)

    # Persona style prompt (manual override / supplement)
    style_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PersonaVideoTag(Base, UUIDMixin, TimestampMixin):
    """Links a video to a persona for training data."""
    __tablename__ = "persona_video_tags"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id"), nullable=False
    )
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id"), nullable=False
    )
    # Whether this video's data has been included in the latest training
    included_in_training: Mapped[bool] = mapped_column(Boolean, default=False)


class PersonaTrainingLog(Base, UUIDMixin, TimestampMixin):
    """Logs each fine-tuning run for a persona."""
    __tablename__ = "persona_training_logs"

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id"), nullable=False
    )
    openai_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="created"
    )  # created | preparing | training | completed | failed | cancelled
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    video_count: Mapped[int] = mapped_column(Integer, default=0)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_hours: Mapped[float] = mapped_column(Float, default=0.0)
    training_examples: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    jsonl_blob_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
