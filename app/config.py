from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aws_region: str = "ap-south-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket: str = ""
    database_url: str = ""

    bedrock_embed_model: str = "amazon.titan-embed-text-v2:0"
    bedrock_chat_model: str = "amazon.nova-lite-v1:0"
    embed_dim: int = 1024
    max_upload_bytes: int = 10485760

    pinecone_api_key: str = ""
    pinecone_index_name: str = "atlas-pinecone-421"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "ap-south-1"
    pinecone_ready_timeout: int = 180
    # BGE-M3 relevant matches in the demo workspace score around 0.50-0.61;
    # 0.65 discarded those answers before generation.
    min_relevance: float = 0.48
    hitl_enabled: bool = True
    memory_turns: int = 6

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"


@lru_cache
def settings() -> Settings:
    return Settings()
