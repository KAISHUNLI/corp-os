from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    redis: str | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    department_code: str | None
    role_code: str
    erp_username: str | None = None

    model_config = {"from_attributes": True}


class TokenUserOut(UserOut):
    access_token: str
    token_type: str = "bearer"


class SsoProviderStatus(BaseModel):
    enabled: bool
    status: str
    app_key_configured: bool | None = None
    issuer: str | None = None
    client_id: str | None = None


class SsoProvidersOut(BaseModel):
    account_password: bool
    dingtalk: SsoProviderStatus
    oidc: SsoProviderStatus


class CitationOut(BaseModel):
    document_id: int
    title: str
    category: str
    snippet: str
    score: float


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


class ChatSessionOut(BaseModel):
    id: int
    title: str
    updated_at: str | None = None
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
    session_only: bool = False
