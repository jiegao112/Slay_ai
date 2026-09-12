from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import DOTENV_PATH


load_dotenv(DOTENV_PATH)


class Settings(BaseSettings):
    """集中读取 .env，代码里不要散落 os.getenv。"""

    model_config = SettingsConfigDict(
        env_file=str(DOTENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Slay AI Backend"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # 用户的 .env 里写的是 QUEN_API；这里同时兼容正确拼写 QWEN_API。
    qwen_api_key: str = Field(default_factory=lambda: os.getenv("QWEN_API") or os.getenv("QUEN_API", ""))
    qwen_base_url: str = Field(default_factory=lambda: os.getenv("QWEN_BASE_URL", ""))
    qwen_vision_model: str = Field(default_factory=lambda: os.getenv("QWEN_VISION_MODEL", "qwen3.7-flash"))
    qwen_embedding_model: str = Field(default_factory=lambda: os.getenv("QWEN_EMBEDDING_MODEL", "qwen3.7-text-embedding"))
    qwen_rerank_model: str = Field(default_factory=lambda: os.getenv("QWEN_RERANK_MODEL", "qwen3-rerank"))
    qwen_rerank_url: str = Field(default_factory=lambda: os.getenv("QWEN_RERANK_URL", ""))

    deepseek_api_key: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_reasoning_model: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-flash"))

    mysql_host: str = Field(default_factory=lambda: os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "127.0.0.1"))
    mysql_port: int = Field(default_factory=lambda: int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")))
    mysql_user: str = Field(default_factory=lambda: os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root"))
    mysql_password: str = Field(default_factory=lambda: os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""))
    mysql_database: str = Field(default_factory=lambda: os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "slay_ai"))

    rag_top_k: int = 5
    rag_final_k: int = 1
    rag_low_confidence_threshold: float = 0.2
    model_timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")))
    model_max_retries: int = Field(default_factory=lambda: int(os.getenv("MODEL_MAX_RETRIES", "1")))

    def validate_required_model_env(self) -> None:
        """启动时做轻量检查，避免第一次请求才发现 key 缺失。"""
        missing = []
        if not self.qwen_api_key:
            missing.append("QWEN_API 或 QUEN_API")
        if not self.qwen_base_url:
            missing.append("QWEN_BASE_URL")
        if not self.deepseek_api_key:
            missing.append("DEEPSEEK_API_KEY")
        if missing:
            raise RuntimeError(f"缺少必要环境变量：{', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
