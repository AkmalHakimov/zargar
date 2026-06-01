from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Zargar Labs"
    database_url: str = "sqlite:///./zargar_demo.db"
    use_mock_llm: bool = True
    llm_provider: str = "mock"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_compatible_base_url: str | None = None
    openai_chat_model: str | None = None
    embedding_provider: str = "hash"
    embedding_dimensions: int = 1536
    telegram_bot_token: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
