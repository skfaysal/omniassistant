import os
from dotenv import find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=find_dotenv(), env_ignore_empty=True, extra="ignore")

    LLM_PROVIDER: str = "openai"  # openai | anthropic | gemini
    MODEL_ID: str | None = None

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None

    LANGSMITH_TRACING: str | None = None
    LANGSMITH_ENDPOINT: str | None = None
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str | None = None


settings = Settings()

# LangSmith SDK reads directly from os.environ, not from the settings object
_langsmith_vars = {
    "LANGSMITH_TRACING": settings.LANGSMITH_TRACING,
    "LANGSMITH_ENDPOINT": settings.LANGSMITH_ENDPOINT,
    "LANGSMITH_API_KEY": settings.LANGSMITH_API_KEY,
    "LANGSMITH_PROJECT": settings.LANGSMITH_PROJECT,
}
for key, value in _langsmith_vars.items():
    if value is not None:
        os.environ[key] = value
