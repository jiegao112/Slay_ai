from __future__ import annotations

from functools import lru_cache

import jieba
from rank_bm25 import BM25Okapi

from app.core.config import Settings, get_settings
from app.core.models import get_model_factory
from app.rag.database import RagDatabase
from app.rag.documents import RagDocument
from app.rag.faiss_store import FaissStore
from app.schemas.game import RagHit


class HybridRetriever:
    """语义召回 + BM25 召回 + qwen rerank。"""

    def __init__(self, source: str, settings: Settings | None = None):
        self.source = source
        self.settings = settings or get_settings()
        self.db = RagDatabase(self.settings)
        self.documents = self.db.load_documents(source)
        self.documents_by_id = {doc.id: doc for doc in self.documents}
        self.faiss_store = FaissStore(source)
        self._bm25 = self._build_bm25(self.documents)

    def retrieve(self, query: str, top_k: int | None = None) -> list[RagHit]:
        top_k = top_k or self.settings.rag_top_k
        semantic_hits = self.faiss_store.search(query, top_k)
        semantic_scores = {hit.id: hit.score for hit in semantic_hits}

        bm25_scores = self._bm25_search(query, top_k)
        candidate_ids = list(dict.fromkeys([*semantic_scores.keys(), *bm25_scores.keys()]))
        if not candidate_ids:
            return []

        candidates = [self._document_for_id(row_id) for row_id in candidate_ids]
        fallback_scores = self._fallback_scores(candidate_ids, semantic_scores, bm25_scores)

        try:
            reranked = get_model_factory().reranker().rerank(
                query=query,
                documents=[doc.rerank_text for doc in candidates],
                top_n=min(top_k, len(candidates)),
            )
            ordered = [
                self._to_hit(candidates[index], score=score, confidence=self._confidence(score))
                for index, score in reranked
                if 0 <= index < len(candidates)
            ]
            if ordered:
                return ordered[:top_k]
        except Exception:
            pass

        sorted_docs = sorted(candidates, key=lambda doc: fallback_scores.get(doc.id, 0.0), reverse=True)
        return [
            self._to_hit(doc, fallback_scores.get(doc.id, 0.0), self._confidence(fallback_scores.get(doc.id, 0.0)))
            for doc in sorted_docs[:top_k]
        ]

    def best(self, query: str) -> RagHit | None:
        hits = self.retrieve(query, self.settings.rag_final_k)
        return hits[0] if hits else None

    def _build_bm25(self, documents: list[RagDocument]) -> BM25Okapi | None:
        if not documents:
            return None
        corpus = [self._tokenize(doc.searchable_text) for doc in documents]
        return BM25Okapi(corpus)

    def _bm25_search(self, query: str, top_k: int) -> dict[int, float]:
        if self._bm25 is None or not self.documents:
            return {}
        scores = self._bm25.get_scores(self._tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda item: float(item[1]), reverse=True)[:top_k]
        return {self.documents[index].id: float(score) for index, score in ranked if float(score) > 0}

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token.strip() for token in jieba.lcut(text) if token.strip()]

    def _document_for_id(self, row_id: int) -> RagDocument:
        doc = self.documents_by_id.get(row_id)
        if doc is not None:
            return doc
        fallback_name = self.faiss_store.fallback_documents().get(row_id, f"{self.source}#{row_id}")
        return RagDocument(id=row_id, name=fallback_name, description="", source=self.source)

    @staticmethod
    def _fallback_scores(
        candidate_ids: list[int],
        semantic_scores: dict[int, float],
        bm25_scores: dict[int, float],
    ) -> dict[int, float]:
        max_semantic = max([abs(v) for v in semantic_scores.values()] or [1.0])
        max_bm25 = max([abs(v) for v in bm25_scores.values()] or [1.0])
        scores: dict[int, float] = {}
        for row_id in candidate_ids:
            semantic = semantic_scores.get(row_id, 0.0) / max_semantic
            bm25 = bm25_scores.get(row_id, 0.0) / max_bm25
            scores[row_id] = semantic * 0.65 + bm25 * 0.35
        return scores

    @staticmethod
    def _confidence(score: float) -> float:
        if score <= 0:
            return 0.0
        return max(0.0, min(1.0, score if score <= 1 else score / (score + 1)))

    @staticmethod
    def _to_hit(doc: RagDocument, score: float, confidence: float) -> RagHit:
        return RagHit(
            id=doc.id,
            name=doc.name,
            description=doc.description,
            effect=doc.effect,
            strategy=doc.strategy,
            fenzu=doc.fenzu,
            source=doc.source,
            score=score,
            confidence=confidence,
        )


@lru_cache(maxsize=8)
def get_retriever(source: str) -> HybridRetriever:
    return HybridRetriever(source)
