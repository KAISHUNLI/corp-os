from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CORP_OS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    database_url: str = "sqlite:///./data/corp_os.db"
    upload_dir: Path = Path("./data/uploads")
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7
    app_name: str = "corp-os"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # Auth: account login is always the baseline; IM providers are optional plugins.
    auth_account_enabled: bool = True
    demo_password: str = "demo123"

    # DingTalk (optional)
    dingtalk_enabled: bool = False
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_redirect_uri: str = "http://127.0.0.1:5173/login"
    dingtalk_mock_enabled: bool = True

    # WeCom / Feishu placeholders (optional; enable when credentials are ready)
    wecom_enabled: bool = False
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_secret: str = ""

    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
