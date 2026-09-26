from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    llm_provider: str = "ollama"
    ollama_model: str = "qwen2.5-coder:1.5b"
    openai_api_key: str | None = None
    openai_base_url: str = "https://openrouter.ai/api/v1"
    openai_model: str = "openai/gpt-4o-mini"
    demo_repo_path: str = "../demo-target"
    database_url: str = "sqlite:///./devloop.db"
    checkpoint_path: str = str(Path(__file__).resolve().parent / "workflow_checkpoints.sqlite")
    allow_local_repositories: bool = True
    access_token: str | None = None

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()