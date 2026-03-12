from typing import Dict, List, Literal, Optional, Union
from enum import Enum
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
import secrets
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE_PATH = PROJECT_ROOT / ".env"


class TextSplitterType(Enum):
    """Enum for different text splitter types."""
    RECURSIVE_BASED = "recursive"
    SENTENCE_BASED = "sentence"
    TOKEN_BASED = "token"

class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Configuration
    LLM_MODEL: str = Field(default="google/gemini-2.5-flash")
    OPENAI_BASE_URL: str = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")

    # Monitoring Configuration
    LANGSMITH_TRACING: bool = Field(default=True)
    LANGSMITH_ENDPOINT: str = Field(default="https://api.smith.langchain.com")
    LANGSMITH_API_KEY: str = Field(default="")
    LANGSMITH_PROJECT: str = Field(default="success-stories-knowledge-base")
    
    # Security Configuration
    JWT_SECRET_KEY: str = Field(default="")
    JWT_ALGORITHM: str = Field(default="HS256")
    ADMIN_PASSWORD: str = Field(default="")
    
    # Enhanced Security Settings
    MAX_LOGIN_ATTEMPTS: int = Field(default=5)
    LOCKOUT_DURATION_MINUTES: int = Field(default=30)
    RATE_LIMIT_WINDOW_MINUTES: int = Field(default=15)
    MAX_REQUESTS_PER_WINDOW: int = Field(default=100)
    REQUIRE_HTTPS: bool = Field(default=False)
    ALLOWED_ORIGINS: List[str] = Field(default=["http://localhost:3000"])
    
    # Database Configuration
    DATABASE_URL: str = Field(default="postgresql://postgres:postgres@langgraph-postgres:5432/postgres")
    POSTGRES_HOST: str = Field(default="langgraph-postgres")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="postgres")
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    
    # Directory configuration
    LOG_DIR: str = Field(default="/app/logs")
    VECTORSTORE_DIR: str = Field(default="/app/vectorstore")
    WORK_DIR: str = Field(default="/app")
    
    # Ingestion configuration
    WEAVIATE_INDEX_NAME: str = Field(default="SuccessStories_Framework")
    WEAVIATE_TEXT_KEY: str = Field(default="text")
    INGESTION_BATCH_SIZE: int = Field(default=1000)
    INGESTION_OVERWRITE: bool = Field(default=False)
    EMBEDDING_MODEL_TYPE: Literal["openai", "huggingface", "openrouter"] = Field(default="openrouter")
    RAG_MAX_RETRIEVED_DOCS: int = Field(default=5)

    # Chunking configuration
    RAG_WITH_CHUNKING: bool = Field(default=True)
    RAG_CHUNK_SIZE: int = Field(default=512)
    RAG_CHUNK_OVERLAP: int = Field(default=128)
    RAG_SPLITTER_TYPE: TextSplitterType = Field(default=TextSplitterType.RECURSIVE_BASED)

    # Embedding model configuration
    EMBEDDING_MODEL_CACHE_DIR: Optional[str] = Field(default=None)
    EMBEDDING_MODEL_NAME: str = Field(default="google/gemini-embedding-001")
    HUGGINGFACE_MODEL_NAME: str = Field(default="Lajavaness/bilingual-embedding-large")

    # Weaviate configuration
    WEAVIATE_HTTP_HOST: str = Field(default="weaviate")
    WEAVIATE_HTTP_PORT: Union[int, str] = Field(default=8080)
    WEAVIATE_GRPC_HOST: str = Field(default="weaviate")
    WEAVIATE_GRPC_PORT: Union[int, str] = Field(default=50051)
    WEAVIATE_API_KEYS: Optional[str] = Field(default="")
    
    # MinIO configuration
    MINIO_ENDPOINT: str = Field(default="minio:9000")
    MINIO_ACCESS_KEY: str = Field(default="")
    MINIO_SECRET_KEY: str = Field(default="")
    MINIO_BUCKET_NAME: str = Field(default="iac-agent")
    MINIO_SECURE: bool = Field(default=False)

    # EMAIL configuration
    CLIENT_ID: str = Field(default="")
    CLIENT_SECRET: str = Field(default="")
    TENANT_ID: str = Field(default="")
    MSAL_AUTHORITY: Optional[str] = Field(default=None)
    MSAL_SCOPE: List[str] = Field(default=["https://graph.microsoft.com/.default"])
    MAIL_USER: str = Field(default="")
    PUBLIC_NOTIFICATION_URL: str = Field(default="https://141.148.30.59/notifications")
    
    # Email polling configuration
    POLLING_INTERVAL_SECONDS: int = Field(default=3600)  # 1 hour
    MAX_EMAILS_PER_POLL: int = Field(default=50)  # Limit to prevent overwhelming the system
    SALESPERSON_EMAIL_MAP_STR: str = Field(
        default="Victoria:antoni.debicki@b-yond.com,Edu:antoni.debicki@b-yond.com,Casen:antoni.debicki@b-yond.com,Javi:antoni.debicki@b-yond.com,Ziad:antoni.debicki@b-yond.com,none:antoni.debicki@b-yond.com"
    )
    _salesperson_email_map: Dict[str, str] = {}

    # Graph API Configuration
    GRAPH_API_BASE_URL: str = Field(default="http://langgraph-api:8000")
    VALUE_REPORTER_GRAPH_ID: str = Field(default="main-orchestrator")
    INGESTION_GRAPH_ID: str = Field(default="ingestion-agent")
    RAG_GRAPH_ID: str = Field(default="RAG-agent")
    EMAIL_GRAPH_ID: str = Field(default="email-ingestion-agent")
    CLASSIFICATION_GRAPH_ID: str = Field(default="email-classification-agent")
    USE_RULE_BASED: bool = Field(default=False)
    USE_LLM_CIQ_PARSING: bool = Field(default=True)
    
    # Processing Configuration
    LOG_LEVEL: str = Field(default="INFO")
    
    @model_validator(mode='after')
    def validate_config(self):
        """Validate the configuration"""
        # Parse salesperson email map from string
        try:
            salesperson_map = {}
            if self.SALESPERSON_EMAIL_MAP_STR:
                for pair in self.SALESPERSON_EMAIL_MAP_STR.split(","):
                    if ":" in pair:
                        salesperson, email = pair.split(":", 1)
                        salesperson_map[salesperson.strip()] = email.strip()
            self._salesperson_email_map = salesperson_map if salesperson_map else {"none": "antoni.debicki@b-yond.com"}
        except Exception as exc:
            logger.warning(f"Failed to parse SALESPERSON_EMAIL_MAP_STR: {exc}")
            self._salesperson_email_map = {"none": "antoni.debicki@b-yond.com"}
        
        # Set MSAL authority if not provided
        if not self.MSAL_AUTHORITY and self.TENANT_ID:
            self.MSAL_AUTHORITY = f"https://login.microsoftonline.com/{self.TENANT_ID}"
        
        # Validate security configuration
        if not self.JWT_SECRET_KEY:
            logger.warning("JWT_SECRET_KEY not set! Generating random key for this session.")
            self.JWT_SECRET_KEY = secrets.token_hex(32)
        elif len(self.JWT_SECRET_KEY) < 32:
            logger.warning("JWT_SECRET_KEY is too short! Should be at least 32 characters.")
            
        # Validate admin password
        if self.ADMIN_PASSWORD and len(self.ADMIN_PASSWORD) < 8:
            logger.warning("ADMIN_PASSWORD is too weak! Should be at least 8 characters.")
            
        return self
    
    @property
    def salesperson_email_map(self) -> Dict[str, str]:
        """Get the parsed salesperson email map."""
        return self._salesperson_email_map if self._salesperson_email_map else {"none": "antoni.debicki@b-yond.com"}
    
    