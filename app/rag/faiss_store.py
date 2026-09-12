from __future__ import annotations

import json
from dataclasses import dataclass

import faiss
import numpy as np
from langchain_openai import OpenAIEmbeddings

from app.core.models import get_model_factory
from app.core.paths import FAISS_DIR


@dataclass(frozen=True)
class SemanticHit:
    id: int
    score: float


class FaissStore:
    """读取已有 indexes/faiss/*.faiss，做语义召回。"""

    def __init__(self, source: str, embeddings: OpenAIEmbeddings | None = None):
        self.source = source
        self.embeddings = embeddings or get_model_factory().embeddings()
        self.index_path = FAISS_DIR / f"{source}.faiss"
        self.meta_path = FAISS_DIR / f"{source}.meta.json"
        self.index = faiss.read_index(str(self.index_path))
        self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))

    def search(self, query: str, top_k: int) -> list[SemanticHit]:
        query_vec = np.asarray(self.embeddings.embed_query(query), dtype="float32").reshape(1, -1)
        if self.meta.get("normalize"):
            faiss.normalize_L2(query_vec)

        raw_scores, raw_ids = self.index.search(query_vec, top_k)
        hits: list[SemanticHit] = []
        for score, row_id in zip(raw_scores[0], raw_ids[0]):
            if int(row_id) == -1:
                continue
            hits.append(SemanticHit(id=int(row_id), score=float(score)))
        return hits

    def fallback_documents(self) -> dict[int, str]:
        """数据库不可用时，至少保留 id，方便调试命中情况。"""
        return {int(row_id): f"{self.source}#{row_id}" for row_id in self.meta.get("ids", [])}

