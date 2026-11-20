from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    llm_base_url: str = 'https://api.deepseek.com'
    llm_api_key: str = ''
    llm_model: str = 'deepseek-chat'
    llm_temperature: float = 0.2

    openai_api_key: str = ''  # used by the eval runner's openai provider

    embedding_base_url: str = ''
    embedding_api_key: str = ''
    embedding_model: str = ''

    # Optional LangSmith tracing (see app/tracing.py)
    langsmith_api_key: str = ''
    langsmith_project: str = 'delivery-copilot'
    langsmith_endpoint: str = ''

    data_dir: Path = Path('data')
    top_k: int = 6

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.embedding_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
