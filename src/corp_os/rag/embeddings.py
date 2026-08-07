"""Embedding providers for corp-os RAG.

Providers:
  - hash: local hashing (tests / offline scaffold)
  - sentence_transformers: local model e.g. BAAI/bge-small-zh-v1.5
  - openai_compatible: OpenAI / 通义 / 本地 vLLM 等兼容 /v1/embeddings
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from functools import lru_cache
from typing import Protocol

import httpx

from corp_os.config import get_settings

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Dependency-free hashing embedder (scaffold / unit tests)."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _tokenize(self, text: str) -> list[str]:
        text = (text or "").lower()
        tokens: list[str] = []
        for span in re.findall(r"[\u4e00-\u9fff]+", text):
            if len(span) == 1:
                tokens.append(span)
            else:
                tokens.append(span)
                tokens.extend(span[i : i + 2] for i in range(len(span) - 1))
        tokens.extend(re.findall(r"[a-z0-9_\-]{2,}", text))
        return tokens

    def _embed_one(self, text: str) -> list[float]:
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0] * self.dim
        counts = Counter(tokens)
        vec = [0.0] * self.dim
        for token, tf in counts.items():
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            idx = int(digest[:8], 16) % self.dim
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vec[idx] += sign * (1.0 + math.log(1 + tf))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


# Backward-compatible name used by older tests/docs.
LocalEmbedder = HashEmbedder


class SentenceTransformerEmbedder:
    """Local sentence-transformers model (default: BGE small Chinese)."""

    def __init__(self, model_name: str, *, query_prefix: str = "") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "未安装 sentence-transformers。请执行: "
                "pip install 'sentence-transformers>=3.0.0'"
            ) from exc

        logger.info("Loading embedding model: %s", model_name)
        self._model = SentenceTransformer(model_name)
        self.query_prefix = query_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        payload = f"{self.query_prefix}{text}" if self.query_prefix else text
        vector = self._model.encode(
            payload,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector.tolist()


class OpenAICompatibleEmbedder:
    """OpenAI-compatible embeddings HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                url,
                headers=headers,
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        items = sorted(data["data"], key=lambda row: row["index"])
        return [row["embedding"] for row in items]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Batch to avoid oversized payloads.
        out: list[list[float]] = []
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            out.extend(self._embed(texts[i : i + batch_size]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    provider = (settings.embedding_provider or "hash").strip().lower()

    if provider in {"hash", "local"}:
        return HashEmbedder(dim=settings.embedding_dim)

    if provider in {"sentence_transformers", "st", "bge"}:
        return SentenceTransformerEmbedder(
            settings.embedding_model,
            query_prefix=settings.embedding_query_prefix,
        )

    if provider in {"openai", "openai_compatible", "api"}:
        if not settings.embedding_api_base:
            raise RuntimeError("CORP_OS_EMBEDDING_API_BASE 未配置")
        return OpenAICompatibleEmbedder(
            base_url=settings.embedding_api_base,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
        )

    raise RuntimeError(
        f"未知 CORP_OS_EMBEDDING_PROVIDER={provider!r}，"
        "可选: hash | sentence_transformers | openai_compatible"
    )


def reset_embedder_cache() -> None:
    get_embedder.cache_clear()
