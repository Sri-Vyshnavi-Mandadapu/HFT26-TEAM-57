"""Hybrid retrieval for Python repository code."""
from __future__ import annotations

import re
from typing import Any, Iterable
from httpx2 import query
import numpy as np

try:
    from rank_bm25 import BM25Plus
except ImportError as exc:
    raise ImportError("Install rank-bm25: pip install rank-bm25") from exc

try:
    from .embeddings import EmbeddingSearch
except ImportError:
    from embeddings import EmbeddingSearch
    

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "for", "with", "by", "and", "or",
    "it", "this", "that", "these", "those", "what", "which", "where",
    "when", "why", "how", "does", "do", "did", "can", "could", "would",
    "should", "please", "show", "find", "tell", "about", "me",
}


def normalize_query(query: str) -> str:
    query = re.sub(
        r"\b(logs?\s+out|logging\s+out|logged\s+out)\b",
        "logout",
        query,
        flags=re.IGNORECASE
    )
    return query

def tokenize(text: Any) -> list[str]:
    """Tokenize prose, paths, and identifiers while retaining whole names."""
    value = str(text or "")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    raw = re.findall(r"[A-Za-z0-9_$]+", value)
    tokens: list[str] = []
    for item in raw:
        lowered = item.lower()
        if len(lowered) > 1 and lowered not in STOPWORDS:
            tokens.append(lowered)
        for part in re.split(r"[_$]+", lowered):
            if len(part) > 1 and part not in STOPWORDS:
                tokens.append(part)
    return tokens or ["__empty__"]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(v) for v in value if v is not None)
    return str(value)


def _record_fields(record: dict[str, Any]) -> tuple[str, str, str]:
    names = " ".join(_text(record.get(k)) for k in (
        "symbol", "qualified_name", "signature", "file_path", "kind"
    )).strip()
    body = " ".join(_text(record.get(k)) for k in ("docstring", "code", "calls")).strip()
    return names, body, f"{names}\n{body}".strip()


class CodeRetriever:
    """BM25 + optional dense retrieval + RRF + explainable boosts."""

    def __init__(
        self,
        indexed_records: Iterable[dict[str, Any]] | None,
        embedding_model: str | None = None,
        use_reranker: bool = False,
        reranker_model: str | None = None,
        use_semantic: bool = True,
        name_weight: float = 1.25,
        body_weight: float = 1.0,
        semantic_weight: float = 1.15,
        rrf_k: int = 60,
        cache_dir: str | None = ".embedding_cache",
    ):
        self.records = [dict(r) for r in (indexed_records or [])]
        self.last_query_info: dict[str, Any] = {}
        self.semantic = None
        self.reranker = None
        self.name_weight = max(0.0, float(name_weight))
        self.body_weight = max(0.0, float(body_weight))
        self.semantic_weight = max(0.0, float(semantic_weight))
        self.RRF_K = max(1, int(rrf_k))

        fields = [_record_fields(r) for r in self.records]
        self.name_documents = [f[0] for f in fields]
        self.body_documents = [f[1] for f in fields]
        self.documents = [f[2] for f in fields]
        self.name_tokens = [tokenize(x) for x in self.name_documents]
        self.body_tokens = [tokenize(x) for x in self.body_documents]
        self.bm25_names = BM25Plus(self.name_tokens) if self.name_tokens else None
        self.bm25_bodies = BM25Plus(self.body_tokens) if self.body_tokens else None

        if use_semantic and self.records:
            try:
                kwargs = {}
                if embedding_model:
                    kwargs["model_name"] = embedding_model
                kwargs["cache_dir"] = cache_dir
                self.semantic = EmbeddingSearch(**kwargs)
                self.semantic.build_index(self.documents)
            except Exception as exc:
                print(f"[retriever] Semantic unavailable; using lexical search: {exc}")
                self.semantic = None

        if use_reranker:
            try:
                try:
                    from .embeddings import Reranker
                except ImportError:
                    from embeddings import Reranker
                self.reranker = Reranker(**({"model_name": reranker_model} if reranker_model else {}))
            except Exception as exc:
                print(f"[retriever] Reranker unavailable: {exc}")
                self.reranker = None

    @staticmethod
    def _scores(index: Any, tokens: list[str], count: int) -> np.ndarray:
        if index is None or not tokens or not count:
            return np.zeros(count, dtype=float)
        values = np.asarray(index.get_scores(tokens), dtype=float).reshape(-1)
        if len(values) != count:
            return np.zeros(count, dtype=float)
        return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

    @staticmethod
    def _exact_identifier_match(query: str, record: dict[str, Any]) -> int:
        """2 exact symbol/qualified name; 1 qualified suffix; 0 otherwise."""
        q = query.strip().lower()
        if not re.fullmatch(r"[a-zA-Z_$][a-zA-Z0-9_$]*(?:\.[a-zA-Z_$][a-zA-Z0-9_$]*)*", q):
            return 0
        symbol = _text(record.get("symbol")).lower()
        qualified = _text(record.get("qualified_name")).lower()
        if q == symbol or q == qualified or qualified.endswith("." + q):
            return 2
        if "." in q and q.rsplit(".", 1)[-1] == symbol:
            return 1
        return 0

    @staticmethod
    def _placeholder_factor(record: dict[str, Any]) -> float:
        code = _text(record.get("code")).strip()
        if not code:
            return 0.98
        meaningful = []
        for line in code.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "//", "/*", "*")):
                continue
            if line.startswith(('"""', "'''")):
                continue
            if re.match(r"^(async\s+)?def\s+|^class\s+", line):
                continue
            meaningful.append(line)
        stub = re.compile(r"(?:pass|\.\.\.|raise\s+NotImplementedError(?:\(\))?|throw\s+new\s+NotImplementedException(?:\(\))?;?)")
        return 0.70 if meaningful and all(stub.fullmatch(line) for line in meaningful) else 1.0

    @staticmethod
    def _field_factors(query_tokens: list[str], record: dict[str, Any]) -> tuple[float, float]:
        query_set = set(query_tokens)
        if not query_set:
            return 1.0, 1.0
        names = set(tokenize(" ".join(_text(record.get(k)) for k in (
            "symbol", "qualified_name", "signature", "kind"
        ))))
        paths = set(tokenize(_text(record.get("file_path"))))
        return (
            1.0 + 0.65 * len(query_set & names) / len(query_set),
            1.0 + 0.20 * len(query_set & paths) / len(query_set),
        )

    @staticmethod
    def _rank_positions(scores: np.ndarray, pool: int, lexical: bool) -> list[int]:
        order = np.argsort(-scores, kind="stable")[:min(max(1, pool), len(scores))]
        return [int(i) for i in order if not lexical or scores[i] > 0]

    def search(
        self,
        query: str,
        top_k: int = 5,
        pool: int = 100,
        rerank_pool: int = 30,
        mode: str = "hybrid",
    ) -> list[dict[str, Any]]:
        """Search with mode='hybrid'|'lexical'|'semantic'; return top code regions."""
        if not query or not str(query).strip() or not self.records or top_k <= 0:
            return []
        if mode not in {"hybrid", "lexical", "semantic"}:
            raise ValueError("mode must be 'hybrid', 'lexical', or 'semantic'")

        query = str(query).strip()
        normalized_query = normalize_query(query)
        query_tokens = tokenize(normalized_query)
        n = len(self.records)
        name_scores = self._scores(self.bm25_names, query_tokens, n)
        body_scores = self._scores(self.bm25_bodies, query_tokens, n)
        dense_scores = None
        if self.semantic is not None and mode in {"hybrid", "semantic"}:
            try:
                dense_scores = np.asarray(self.semantic.search(normalized_query), dtype=float).reshape(-1)
                if len(dense_scores) != n:
                    dense_scores = None
                else:
                    dense_scores = np.nan_to_num(dense_scores, nan=0.0, posinf=0.0, neginf=0.0)
            except Exception as exc:
                print(f"[retriever] Semantic search failed; lexical fallback: {exc}")

        lexical_on = mode in {"hybrid", "lexical"} or (mode == "semantic" and dense_scores is None)
        sources: list[tuple[str, np.ndarray, float, bool]] = []
        if lexical_on:
            sources += [("names", name_scores, self.name_weight, True),
                        ("body", body_scores, self.body_weight, True)]
        if dense_scores is not None and mode in {"hybrid", "semantic"}:
            sources.append(("semantic", dense_scores, self.semantic_weight, False))

        fused = np.zeros(n, dtype=float)
        source_ranks: dict[str, dict[int, int]] = {}
        for source, scores, weight, is_lexical in sources:
            positions = self._rank_positions(scores, pool, is_lexical)
            source_ranks[source] = {idx: rank for rank, idx in enumerate(positions, 1)}
            for rank, idx in enumerate(positions, 1):
                fused[idx] += weight / (self.RRF_K + rank)

        for idx, record in enumerate(self.records):
            exact = self._exact_identifier_match(query, record)
            if exact == 2:
                fused[idx] *= 1.75
            elif exact == 1:
                fused[idx] *= 1.20
            name_factor, path_factor = self._field_factors(query_tokens, record)
            fused[idx] *= name_factor * path_factor * self._placeholder_factor(record)

        # Ensure exact function-name matches are included,
        # even when BM25 produces zero scores.
        for idx, record in enumerate(self.records):
            if self._exact_identifier_match(query, record) > 0 and fused[idx] <= 0:
                fused[idx] = 1.0

        candidates = [
            int(i)
            for i in np.argsort(-fused, kind="stable")
            if fused[i] > 0
        ]
        reranker_used = False
        if self.reranker is not None and candidates:
            subset = candidates[:min(len(candidates), max(top_k, rerank_pool))]
            try:
                scores = np.asarray(self.reranker.score(query, [self.documents[i] for i in subset]), dtype=float).reshape(-1)
                if len(scores) == len(subset) and np.all(np.isfinite(scores)):
                    base_rank = {idx: rank for rank, idx in enumerate(subset, 1)}
                    rerank_order = np.argsort(-scores, kind="stable")
                    model_rank = {subset[pos]: rank for rank, pos in enumerate(rerank_order, 1)}
                    subset.sort(key=lambda idx: 0.70 / (self.RRF_K + base_rank[idx]) + 0.30 / (self.RRF_K + model_rank[idx]), reverse=True)
                    chosen = set(subset)
                    candidates = subset + [idx for idx in candidates if idx not in chosen]
                    reranker_used = True
            except Exception as exc:
                print(f"[retriever] Reranking failed; preserving original ranking: {exc}")

        self.last_query_info = {
            "query": query, "tokens": query_tokens, "mode": mode,
            "semantic_available": dense_scores is not None,
            "reranker_used": reranker_used, "source_ranks": source_ranks,
        }
        results = []
        for rank, idx in enumerate(candidates[:top_k], 1):
            result = dict(self.records[idx])
            evidence = " ".join(_text(result.get(k)) for k in (
                "symbol", "qualified_name", "signature", "file_path", "docstring", "code", "calls"
            ))
            matched = sorted(set(query_tokens) & set(tokenize(evidence)))
            exact = self._exact_identifier_match(query, result)
            hit_sources = [name for name, rank_map in source_ranks.items() if idx in rank_map]
            result.update({
                "rank": rank,
                "score": round(float(fused[idx]), 8),
                "lexical_score": round(float(max(name_scores[idx], body_scores[idx])), 6),
                "semantic_score": round(float(dense_scores[idx]), 6) if dense_scores is not None else None,
                "matched_terms": matched,
                "retrieval_sources": hit_sources,
                "confidence": "strong" if exact == 2 or len(hit_sources) >= 3 else "moderate" if matched or hit_sources else "weak",
                "evidence": "Exact identifier match" if exact == 2 else ("Matched terms: " + ", ".join(matched) if matched else "Semantic match; verify the returned source snippet"),
            })
            results.append(result)
        return results
