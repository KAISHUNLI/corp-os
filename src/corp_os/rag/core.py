from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter


def chunk_text(text: str, *, chunk_size: int = 420, overlap: int = 80) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    # Prefer paragraph splits first
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not parts:
        parts = [text]

    chunks: list[str] = []
    buf = ""
    for part in parts:
        if len(buf) + len(part) + 1 <= chunk_size:
            buf = f"{buf}\n{part}".strip()
            continue
        if buf:
            chunks.append(buf)
        if len(part) <= chunk_size:
            buf = part
        else:
            start = 0
            while start < len(part):
                end = min(len(part), start + chunk_size)
                chunks.append(part[start:end])
                if end >= len(part):
                    break
                start = max(0, end - overlap)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _tokenize(text: str) -> list[str]:
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


class LocalEmbedder:
    """Dependency-free local embedding for company RAG scaffold.

    Uses hashing trick over tokens. Swap to OpenAI/DashScope/etc. later
    by implementing the same embed_documents/embed_query interface.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self.dim
        counts = Counter(tokens)
        vec = [0.0] * self.dim
        for token, tf in counts.items():
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            idx = int(digest[:8], 16) % self.dim
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vec[idx] += sign * (1.0 + math.log(1 + tf))
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def dumps_embedding(vec: list[float]) -> str:
    return json.dumps(vec)


def loads_embedding(raw: str) -> list[float]:
    try:
        data = json.loads(raw or "[]")
        return [float(x) for x in data]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
