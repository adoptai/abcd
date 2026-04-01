"""Pydantic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


# ── Project ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    base_url: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    base_url: str | None = None
    status: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    base_url: str = ""
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetail(ProjectOut):
    processes: list["ProcessOut"] = []


# ── Process ──────────────────────────────────────────────────────────────────

class ProcessCreate(BaseModel):
    name: str
    description: str = ""
    base_url: str = ""


class ProcessUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    base_url: str | None = None
    status: str | None = None


class ProcessOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: str
    base_url: str = ""
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Message ──────────────────────────────────────────────────────────────────

class MessageCreate(BaseModel):
    project_id: str | None = None
    process_id: str | None = None
    role: str = Field(pattern=r"^(human|agent)$")
    content_type: str = "text"
    content: str
    url: str = ""


class MessageOut(BaseModel):
    id: str
    project_id: str | None
    process_id: str | None
    chat_session_id: str | None = None
    role: str
    content_type: str
    content: str
    url: str = ""
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── ChatSession ─────────────────────────────────────────────────────────

class ChatSessionCreate(BaseModel):
    project_id: str
    process_id: str | None = None
    title: str = "New Chat"


class ChatSessionUpdate(BaseModel):
    title: str


class ChatSessionOut(BaseModel):
    id: str
    project_id: str
    process_id: str | None
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Screenshot ───────────────────────────────────────────────────────────────

class ScreenshotOut(BaseModel):
    id: str
    project_id: str | None
    process_id: str | None
    file_path: str
    url: str
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── CaptureSession ──────────────────────────────────────────────────────────

class CaptureSessionCreate(BaseModel):
    click_tracking: bool = True
    url_monitoring: bool = True
    har_capture: bool = True
    voice_mode: str = "narrate"
    narration: bool = False
    capture_purpose: str = "general"
    login_url: str | None = None
    app_name: str | None = None


class CaptureSessionUpdate(BaseModel):
    click_tracking: bool | None = None
    url_monitoring: bool | None = None
    har_capture: bool | None = None
    voice_mode: str | None = None
    narration: bool | None = None
    capture_purpose: str | None = None
    login_url: str | None = None
    app_name: str | None = None


class CaptureSessionOut(BaseModel):
    id: str
    process_id: str
    project_id: str
    status: str
    click_tracking: bool
    url_monitoring: bool
    har_capture: bool
    voice_mode: str
    narration: bool
    har_file_path: str | None
    started_at: datetime | None
    stopped_at: datetime | None
    capture_purpose: str
    login_url: str | None
    app_name: str | None

    model_config = {"from_attributes": True}


# ── Narration ───────────────────────────────────────────────────────────────

class NarrationCreate(BaseModel):
    project_id: str
    process_id: str | None = None
    capture_session_id: str | None = None
    content: str
    url: str = ""


class NarrationOut(BaseModel):
    id: str
    project_id: str
    process_id: str | None
    capture_session_id: str | None
    content: str
    url: str
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── ClickEvent ──────────────────────────────────────────────────────────────

class ClickEventCreate(BaseModel):
    project_id: str | None = None
    process_id: str | None = None
    capture_session_id: str | None = None
    event_type: str = "click"  # click | input | change | submit
    url: str = ""
    tag_name: str
    element_id: str | None = None
    class_name: str | None = None
    text_content: str | None = None
    href: str | None = None
    selector: str = ""
    x: int = 0
    y: int = 0
    input_type: str | None = None
    value: str | None = None
    field_name: str | None = None
    field_role: str | None = None
    is_redacted: bool = False
    autocomplete: str | None = None
    placeholder: str | None = None
    aria_label: str | None = None
    role_attr: str | None = None
    data_attrs_json: str | None = None
    timestamp: datetime | None = None


class ClickEventOut(BaseModel):
    id: str
    project_id: str | None
    process_id: str | None
    capture_session_id: str | None
    event_type: str
    url: str
    tag_name: str
    element_id: str | None
    class_name: str | None
    text_content: str | None
    href: str | None
    selector: str
    x: int
    y: int
    input_type: str | None
    value: str | None
    field_name: str | None
    field_role: str | None
    is_redacted: bool
    autocomplete: str | None
    placeholder: str | None
    aria_label: str | None
    role_attr: str | None
    data_attrs_json: str | None
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── TimelineEvent ───────────────────────────────────────────────────────────

class TimelineEventCreate(BaseModel):
    project_id: str
    process_id: str | None = None
    capture_session_id: str | None = None
    event_type: str
    source_id: str | None = None
    summary: str = ""
    metadata_json: str = "{}"


class TimelineEventOut(BaseModel):
    id: str
    project_id: str
    process_id: str | None
    capture_session_id: str | None
    event_type: str
    source_id: str | None
    summary: str
    metadata_json: str
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── Document ───────────────────────────────────────────────────────────────

class DocumentCreate(BaseModel):
    title: str
    content: str = ""
    doc_type: str = "user"
    process_id: str | None = None


class DocumentUpdate(BaseModel):
    title: str | None = None
    content: str | None = None


class DocumentOut(BaseModel):
    id: str
    project_id: str
    process_id: str | None
    title: str
    content: str
    doc_type: str
    is_canonical: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Attachment ─────────────────────────────────────────────────────────────

class AttachmentOut(BaseModel):
    id: str
    project_id: str
    process_id: str | None
    filename: str
    file_path: str
    content_type: str
    file_size: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Question ──────────────────────────────────────────────────────────────

class QuestionCreate(BaseModel):
    content: str
    process_id: str | None = None
    context_url: str = ""


class QuestionUpdate(BaseModel):
    content: str | None = None
    answer: str | None = None
    status: str | None = None  # open | answered | resolved
    context_url: str | None = None


class QuestionOut(BaseModel):
    id: str
    project_id: str
    process_id: str | None
    content: str
    answer: str
    status: str
    context_url: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── LoginSession (extends CaptureSession for login recording) ───────────────

class LoginSessionCreate(BaseModel):
    app_name: str
    login_url: str
    project_id: str | None = None
    process_id: str | None = None


class LoginSessionOut(CaptureSessionOut):
    pass


class LoginDraftOut(BaseModel):
    id: str
    capture_session_id: str
    bundle_json: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
