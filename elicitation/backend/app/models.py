"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    processes: Mapped[list["Process"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    screenshots: Mapped[list["Screenshot"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    capture_sessions: Mapped[list["CaptureSession"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    narrations: Mapped[list["Narration"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    questions: Mapped[list["Question"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Process(Base):
    __tablename__ = "processes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="processes")
    messages: Mapped[list["Message"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    screenshots: Mapped[list["Screenshot"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    capture_sessions: Mapped[list["CaptureSession"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    narrations: Mapped[list["Narration"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    questions: Mapped[list["Question"]] = relationship(back_populates="process", cascade="all, delete-orphan")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="process", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    chat_session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True)
    role: Mapped[str] = mapped_column(String(20))  # human | agent
    content_type: Mapped[str] = mapped_column(String(50), default="text")  # text | voice_transcript
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2000), default="")
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project | None"] = relationship(back_populates="messages")
    process: Mapped["Process | None"] = relationship(back_populates="messages")
    chat_session: Mapped["ChatSession | None"] = relationship(back_populates="messages")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="chat_sessions")
    process: Mapped["Process | None"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["Message"]] = relationship(back_populates="chat_session", cascade="all, delete-orphan")


class Screenshot(Base):
    __tablename__ = "screenshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    file_path: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(2000), default="")
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project | None"] = relationship(back_populates="screenshots")
    process: Mapped["Process | None"] = relationship(back_populates="screenshots")


class CaptureSession(Base):
    __tablename__ = "capture_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    process_id: Mapped[str] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle | capturing | paused | stopped
    click_tracking: Mapped[bool] = mapped_column(Boolean, default=True)
    url_monitoring: Mapped[bool] = mapped_column(Boolean, default=True)
    har_capture: Mapped[bool] = mapped_column(Boolean, default=True)
    voice_mode: Mapped[str] = mapped_column(String(20), default="narrate")  # narrate | chat
    narration: Mapped[bool] = mapped_column(Boolean, default=False)
    capture_purpose: Mapped[str] = mapped_column(String(50), default="general")  # general | login_recording
    login_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    app_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    har_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(nullable=True)

    process: Mapped["Process"] = relationship(back_populates="capture_sessions")
    project: Mapped["Project"] = relationship(back_populates="capture_sessions")
    narrations: Mapped[list["Narration"]] = relationship(back_populates="capture_session", cascade="all, delete-orphan")


class Narration(Base):
    __tablename__ = "narrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    capture_session_id: Mapped[str | None] = mapped_column(ForeignKey("capture_sessions.id", ondelete="CASCADE"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2000), default="")
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="narrations")
    process: Mapped["Process | None"] = relationship(back_populates="narrations")
    capture_session: Mapped["CaptureSession | None"] = relationship(back_populates="narrations")


class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    capture_session_id: Mapped[str | None] = mapped_column(ForeignKey("capture_sessions.id", ondelete="CASCADE"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), default="click")  # click | input | change | submit
    url: Mapped[str] = mapped_column(String(2000), default="")
    tag_name: Mapped[str] = mapped_column(String(50))
    element_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    class_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text_content: Mapped[str | None] = mapped_column(String(100), nullable=True)
    href: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    selector: Mapped[str] = mapped_column(String(500), default="")
    x: Mapped[int] = mapped_column(Integer, default=0)
    y: Mapped[int] = mapped_column(Integer, default=0)
    input_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # text, select, checkbox, etc.
    value: Mapped[str | None] = mapped_column(String(500), nullable=True)  # field value
    field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # input name attribute
    field_role: Mapped[str | None] = mapped_column(String(30), nullable=True)  # username | password | otp | unknown_sensitive
    is_redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    autocomplete: Mapped[str | None] = mapped_column(String(100), nullable=True)
    placeholder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    aria_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_attr: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data_attrs_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {"data-testid": "..."}
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    capture_session_id: Mapped[str | None] = mapped_column(ForeignKey("capture_sessions.id", ondelete="CASCADE"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50))
    # event_type: message, screenshot, click, narration, url_change,
    #             capture_start, capture_stop, har_start, har_stop
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, default="")
    doc_type: Mapped[str] = mapped_column(String(20), default="user")  # canonical | user
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="documents")
    process: Mapped["Process | None"] = relationship(back_populates="documents")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    filename: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="attachments")
    process: Mapped["Process | None"] = relationship(back_populates="attachments")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    process_id: Mapped[str | None] = mapped_column(ForeignKey("processes.id", ondelete="CASCADE"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | answered | resolved
    context_url: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="questions")
    process: Mapped["Process | None"] = relationship(back_populates="questions")


class LoginDraft(Base):
    __tablename__ = "login_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    capture_session_id: Mapped[str] = mapped_column(ForeignKey("capture_sessions.id", ondelete="CASCADE"))
    bundle_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)
