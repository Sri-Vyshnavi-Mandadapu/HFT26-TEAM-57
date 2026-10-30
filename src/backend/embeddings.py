from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Sequence

import numpy as np


DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def _prefixes(model_name: str) -> tuple[str, str]:
    
    name = model_name.lower()

    if "bge" in name and "reranker" not in name:
        prefix = (
            "Represent this sentence for searching "
            "relevant passages: "
        )

        return prefix, prefix

    if "nomic" in name:
        return "search_query: ", "search_document: "

    if "e5" in name:
        return "query: ", "passage: "

    return "", ""


class EmbeddingSearch:
    

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        max_seq_length: int = 512,
        cache_dir: str | None = ".embedding_cache",
        batch_size: int = 32,
    ):

        try:
            from sentence_transformers import SentenceTransformer

        except ImportError as exc:

            raise ImportError(
                "Semantic retrieval requires sentence-transformers. "
                "Install it using: pip install sentence-transformers"
            ) from exc

        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = max(1, int(batch_size))

        model_kwargs = {}

        if any(
            tag in model_name.lower()
            for tag in ("jina", "nomic")
        ):
            model_kwargs["trust_remote_code"] = True

        self.model = SentenceTransformer(
            model_name,
            **model_kwargs,
        )

        if max_seq_length:
            self.model.max_seq_length = int(max_seq_length)

        self.query_prefix, self.doc_prefix = _prefixes(
            model_name
        )

        self.embeddings: np.ndarray | None = None

        self._cache_identity: str | None = None

 

    def _cache_path(
        self,
        texts: Sequence[str],
    ) -> str | None:

        if not self.cache_dir:
            return None

        digest = hashlib.sha256()

        digest.update(
            self.model_name.encode("utf-8")
        )

        digest.update(
            str(self.model.max_seq_length).encode("ascii")
        )

        for text in texts:

            digest.update(
                text.encode("utf-8", "ignore")
            )

            digest.update(b"\0")

        return os.path.join(
            self.cache_dir,
            digest.hexdigest() + ".npy",
        )


    def build_index(
        self,
        texts: Sequence[str],
    ) -> None:

        texts = [
            str(text or "")
            for text in texts
        ]

        cache_path = self._cache_path(texts)

        self._cache_identity = cache_path

        # Reuse cached embeddings if valid.
        if cache_path and os.path.isfile(cache_path):

            try:

                cached = np.load(
                    cache_path,
                    allow_pickle=False,
                )

                if (
                    cached.ndim == 2
                    and cached.shape[0] == len(texts)
                ):

                    self.embeddings = np.asarray(
                        cached,
                        dtype=np.float32,
                    )

                    return

            except (OSError, ValueError):

                # Invalid cache: rebuild embeddings.
                pass

        # Handle empty repositories.
        if not texts:

            dimension = (
                self.model.get_sentence_embedding_dimension()
            )

            self.embeddings = np.zeros(
                (0, int(dimension or 0)),
                dtype=np.float32,
            )

            return

        # Generate normalized embeddings.
        vectors = self.model.encode(
            [
                self.doc_prefix + text
                for text in texts
            ],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 200,
        )

        self.embeddings = np.asarray(
            vectors,
            dtype=np.float32,
        )

        # Save embeddings for future runs.
        if cache_path:

            Path(cache_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temporary = cache_path + ".tmp"

            # Use a file handle to prevent NumPy from adding
            # an unwanted .npy extension.
            with open(temporary, "wb") as handle:

                np.save(
                    handle,
                    self.embeddings,
                    allow_pickle=False,
                )

            os.replace(
                temporary,
                cache_path,
            )

    def encode_words(
        self,
        words: Sequence[str],
    ) -> np.ndarray:

        """
        Generate normalized embeddings for a list of words.

        Useful when other parts of the retrieval engine
        require direct access to embedding vectors.
        """

        vectors = self.model.encode(
            list(words),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        return np.asarray(
            vectors,
            dtype=np.float32,
        )

   

    def search(
        self,
        query: str,
    ) -> np.ndarray:

        """
        Return cosine similarity scores for all indexed
        code documents.
        """

        if self.embeddings is None:

            raise RuntimeError(
                "Call build_index() before search()."
            )

        if self.embeddings.shape[0] == 0:

            return np.zeros(
                0,
                dtype=np.float32,
            )


        query_embedding = self.model.encode(
            [
                self.query_prefix + str(query)
            ],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]

        # Embeddings are normalized.
        # Dot product is equivalent to cosine similarity.
        scores = self.embeddings @ query_embedding

        return np.asarray(
            scores,
            dtype=np.float32,
        )




class Reranker:
    """
    Cross-encoder that evaluates a query and code snippet
    together.

    More accurate than embeddings alone for many retrieval
    tasks, but slower.

    Use it only on a limited candidate pool.
    """

    def __init__(
        self,
        model_name: str = (
            "cross-encoder/ms-marco-MiniLM-L-6-v2"
        ),
        max_length: int = 512,
    ):

        try:

            from sentence_transformers import CrossEncoder

        except ImportError as exc:

            raise ImportError(
                "Cross-encoder reranking requires "
                "sentence-transformers."
            ) from exc

        self.model = CrossEncoder(
            model_name,
            max_length=max_length,
        )


    def score(
        self,
        query: str,
        docs: Sequence[str],
    ) -> list[float]:

        """
        Return relevance scores for query-document pairs.

        These are raw relevance scores. The retriever
        combines their ranks instead of assuming their
        numerical scale matches BM25 or embedding scores.
        """

        if not docs:
            return []

        raw_scores = np.asarray(
            self.model.predict(
                [
                    (str(query), str(doc))
                    for doc in docs
                ],
                show_progress_bar=False,
            ),
            dtype=float,
        ).reshape(-1)

        return [
            float(value)
            if math.isfinite(float(value))
            else 0.0
            for value in raw_scores
        ]

