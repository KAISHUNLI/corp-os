from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    # src/corp_os/config.py → repo root
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CORP_OS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    database_url: str = (
        "postgresql+psycopg://corpos:corpos_dev_password@127.0.0.1:5432/corp_os"
    )
    upload_dir: Path = Path("./data/uploads")
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7
    app_name: str = "corp-os"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    auth_account_enabled: bool = True
    demo_password: str = "demo123"

    # Security baseline (step 9)
    login_rate_limit_per_minute: int = 20
    max_upload_bytes: int = 20 * 1024 * 1024
    # JSON object: {"erp_login": "password", ...} for mapped ERP identities
    erp_credential_map: str = '{"admin":"admin"}'
    # service=always service account; mapped=must bind; hybrid=bind first else elevated→service
    erp_identity_mode: str = "hybrid"

    # SSO stubs (real IdP wiring later)
    sso_dingtalk_enabled: bool = False
    sso_dingtalk_app_key: str = ""
    sso_oidc_enabled: bool = False
    sso_oidc_issuer: str = ""
    sso_oidc_client_id: str = ""

    redis_url: str = "redis://127.0.0.1:6379/0"
    # false = never connect (tests / offline); true = use redis_url with memory fallback
    redis_enabled: bool = True
    erp_token_ttl_seconds: int = 3600
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "corp_os_chunks"
    # postgres = vectors in document_chunks.embedding_json; milvus = vectors in Milvus
    vector_store: str = "milvus"

    # Embedding (step 3). hash = tests/offline; sentence_transformers = local BGE; openai_compatible = API.
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 256  # hash provider
    embedding_vector_dim: int = 512  # BGE-small-zh / milvus collection dim
    # BGE retrieval often benefits from a query instruction prefix.
    embedding_query_prefix: str = "为这个句子生成表示以用于检索："
    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_min_score: float = 0.25
    # HuggingFace mirror for local sentence-transformers downloads (empty = default hub)
    hf_endpoint: str = "https://hf-mirror.com"

    # Chat LLM (step 5). off = template answers; openai_compatible = /chat/completions
    llm_provider: str = "off"
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen-plus"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60.0
    # Prior user/assistant messages injected into LLM (0 = disable conversational memory).
    chat_history_max_messages: int = 16

    # Agent (step 8). auto = tool-calling when LLM on AND intent off/agent forced; rules|agent force.
    agent_mode: str = "auto"
    agent_max_steps: int = 6

    # Intent LLM (before RAG). auto=on when LLM enabled; off=rules only; on=require LLM classify.
    intent_llm_mode: str = "auto"
    intent_min_confidence: float = 0.35

    # company-er ERP connector (step 7). Off by default until ERP is running.
    erp_enabled: bool = False
    erp_base_url: str = "http://127.0.0.1:8002"
    erp_api_prefix: str = "/api/v1"
    erp_username: str = "admin"
    erp_password: str = "admin"
    erp_timeout_seconds: float = 20.0
    # Empty = {erp_base_url}/openapi.json
    erp_openapi_url: str = ""
    erp_call_list_limit: int = 15
    erp_call_max_chars: int = 6000

    @model_validator(mode="after")
    def _absolutize_upload_dir(self) -> "Settings":
        """Relative upload_dir must not depend on process cwd (avoids generated-file 404)."""
        path = self.upload_dir
        if not path.is_absolute():
            path = (_project_root() / path).resolve()
        else:
            path = path.resolve()
        self.upload_dir = path
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
