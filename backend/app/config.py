from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ADHOC_IMAGE_MODEL_OPTIONS = (
    {'value': 'gpt-5-mini', 'label': 'GPT-5 Mini'},
    {'value': 'gpt-4.1-mini', 'label': 'GPT-4.1 Mini'},
    {'value': 'nemotron-nano', 'label': 'Nemotron Nano VL (Free)'},
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Advanced RAG Chatbot API'
    app_env: str = 'dev'
    app_host: str = '0.0.0.0'
    app_port: int = 8000
    database_url: str = Field(default='postgresql://knowledgehub:knowledgehub@postgres:5432/knowledgehub')
    admin_username: str = Field(default='admin')
    admin_password: str = Field(default='')

    upload_dir: Path = Path('data/uploads')
    max_upload_size_mb: int = 30

    chunk_size: int = 900
    chunk_overlap: int = 150

    qdrant_url: str = 'http://qdrant:6333'
    qdrant_api_key: str = Field(default='')
    qdrant_collection: str = 'rag_chunks'
    qdrant_image_collection: str = 'rag_image_chunks'

    retrieval_top_k: int = 20
    image_retrieval_top_k: int = 8
    rerank_top_k: int = 5
    query_expansion_count: int = 3
    evaluation_max_rows: int = 100

    openai_api_key: str = Field(default='')
    openai_base_url: str = Field(default='https://api.openai.com/v1')
    openai_model: str = Field(default='gpt-5-mini-2025-08-07')
    evaluation_model: str = Field(default='gpt-4.1-nano-2025-04-14')
    embedding_model: str = Field(default='text-embedding-3-large')
    temperature: float = 0.1
    
    openrouter_api_key: str = Field(default='')
    openrouter_base_url: str = Field(default='https://openrouter.ai/api/v1')
    openrouter_model: str = Field(default='nvidia/nemotron-nano-12b-v2-vl:free')

    clip_model_name: str = Field(default='clip-ViT-B-32')
    adhoc_image_models: tuple[str, ...] = ('gpt-5-mini', 'gpt-4.1-mini', 'nemotron-nano')

    cohere_api_key: str = Field(default='')
    cohere_rerank_model: str = 'rerank-v3.5'

    jwt_secret_key: str = Field(default='change-this-secret-key-in-production')

    @property
    def adhoc_image_model_options(self) -> tuple[dict[str, str], ...]:
        return ADHOC_IMAGE_MODEL_OPTIONS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings
