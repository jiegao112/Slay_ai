from __future__ import annotations

from functools import lru_cache
from typing import Sequence

import httpx
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import Settings, get_settings


class QwenReranker:
    """Qwen rerank 的轻封装。

    不同兼容网关的 rerank 路径可能不同，因此支持用 QWEN_RERANK_URL 显式覆盖。
    若接口不可用，调用方会降级到语义/BM25 的混合排序。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []

        url = self.settings.qwen_rerank_url or f"{self.settings.qwen_base_url.rstrip('/')}/rerank"
        payload = {
            "model": self.settings.qwen_rerank_model,
            "query": query,
            "documents": list(documents),
            "top_n": min(top_n, len(documents)),
        }
        headers = {
            "Authorization": f"Bearer {self.settings.qwen_api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=30) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        # 兼容常见返回：{"results":[{"index":0,"relevance_score":0.9}]}
        raw_results = data.get("results") or data.get("data", {}).get("results") or []
        reranked: list[tuple[int, float]] = []
        for item in raw_results:
            index = int(item.get("index", item.get("document_index", 0)))
            score = float(item.get("relevance_score", item.get("score", 0.0)))
            reranked.append((index, score))
        return reranked[:top_n]


class ModelFactory:
    """LangChain 模型统一入口。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    @lru_cache(maxsize=1)
    def vision_llm(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.settings.qwen_vision_model,
            api_key=self.settings.qwen_api_key,
            base_url=self.settings.qwen_base_url,
            temperature=0,
            extra_body={"enable_thinking": False},
            timeout=self.settings.model_timeout_seconds,
            max_retries=self.settings.model_max_retries,
        )

    @lru_cache(maxsize=1)
    def reasoning_llm(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.settings.deepseek_reasoning_model,
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            temperature=0.2,
            streaming=True,
            timeout=self.settings.model_timeout_seconds,
            max_retries=self.settings.model_max_retries,
        )

    @lru_cache(maxsize=1)
    def embeddings(self) -> OpenAIEmbeddings:
        return OpenAIEmbeddings(
            model=self.settings.qwen_embedding_model,
            api_key=self.settings.qwen_api_key,
            base_url=self.settings.qwen_base_url,
            check_embedding_ctx_length=False,
            timeout=self.settings.model_timeout_seconds,
            max_retries=self.settings.model_max_retries,
        )

    @lru_cache(maxsize=1)
    def reranker(self) -> QwenReranker:
        return QwenReranker(self.settings)


@lru_cache(maxsize=1)
def get_model_factory() -> ModelFactory:
    return ModelFactory(get_settings())
