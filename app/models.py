from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str  # "system", "user", "assistant", or "tool"
    content: str
    images: list[str] | None = None
    tool_calls: list[dict] | None = None


class WhatsAppMessage(BaseModel):
    from_number: str
    message_id: str
    timestamp: str
    text: str
    type: str
    media_id: str | None = None
    reply_to_message_id: str | None = None


class HealthResponse(BaseModel):
    status: str


class ReadinessChecks(BaseModel):
    db: str
    ollama: str


class ReadinessResponse(BaseModel):
    status: str
    checks: ReadinessChecks


class Note(BaseModel):
    id: int
    title: str
    content: str
    created_at: str = ""


class Memory(BaseModel):
    id: int
    content: str
    category: str | None = None
    active: bool = True
    created_at: str = ""


class Project(BaseModel):
    id: int
    phone_number: str
    name: str
    description: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""


class ProjectTask(BaseModel):
    id: int
    project_id: int
    title: str
    description: str = ""
    status: str = "pending"
    priority: str = "medium"
    due_date: str | None = None
    created_at: str = ""
    updated_at: str = ""


class ProjectNote(BaseModel):
    id: int
    project_id: int
    title: str | None = None
    content: str
    created_at: str = ""


class WhatsAppReaction(BaseModel):
    from_number: str
    reacted_message_id: str  # wa_message_id del mensaje que recibió la reacción
    emoji: str
