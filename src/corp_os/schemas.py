from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str
    app: str
    version: str


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    department_code: str | None
    role_code: str

    model_config = {"from_attributes": True}


class TokenUserOut(UserOut):
    access_token: str
    token_type: str = "bearer"


class CitationOut(BaseModel):
    document_id: int
    title: str
    category: str
    snippet: str
    score: float
