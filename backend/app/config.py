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
    telegram_allowed_user_ids: str = ""
    telegram_allowed_chat_ids: str = ""
    github_token: str | None = None
    github_allowed_repos: str | None = None
    github_draft_prs_only: bool = True
    github_max_files_changed: int = 5
    github_max_lines_changed: int = 300
    github_max_new_files: int = 3
    max_files_changed: int = 5
    max_lines_changed: int = 300
    max_new_files: int = 3
    branch_retention_days: int = 7
    developer_agent_mode: str = "plan_only"
    developer_coding_engine: str = "deterministic"
    codex_cli_path: str = "codex"
    developer_complex_min_source_files: int = 2
    developer_complex_min_source_lines: int = 20
    developer_frontend_complex_min_source_files: int = 2
    developer_multipage_min_relevant_source_files: int = 2
    max_changed_files_for_mvp: int = 30
    max_changed_source_lines_for_mvp: int = 2500
    max_allowed_directories_for_mvp: int = 8
    allow_complex_repo_tasks: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
