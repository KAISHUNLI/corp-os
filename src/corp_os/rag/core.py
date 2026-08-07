from __future__ import annotations

import json
import re
from pathlib import Path


# Short OCR / tickets / one-page notices: keep as one retrieval unit.
SHORT_DOC_CHARS = 800
# Tabular: keep header + N data rows per chunk (or soft char cap).
TABULAR_ROWS_PER_CHUNK = 20
TABULAR_MAX_CHARS = 1400


def chunk_text(text: str, *, chunk_size: int = 420, overlap: int = 80) -> list[str]:
    """Paragraph-first sliding window — good for policies / long prose."""
    text = (text or "").strip()
    if not text:
        return []
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


def chunk_short(text: str) -> list[str]:
    text = (text or "").strip()
    return [text] if text else []


def chunk_tabular(
    text: str,
    *,
    rows_per_chunk: int = TABULAR_ROWS_PER_CHUNK,
    max_chars: int = TABULAR_MAX_CHARS,
) -> list[str]:
    """Keep sheet/header context with each row group (xlsx/csv style extracts)."""
    text = (text or "").strip()
    if not text:
        return []

    sheets = re.split(r"(?=^# 工作表：)", text, flags=re.MULTILINE)
    sheets = [s.strip() for s in sheets if s.strip()]
    if not sheets:
        sheets = [text]

    chunks: list[str] = []
    for sheet in sheets:
        lines = [ln for ln in sheet.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0] if lines[0].startswith("# 工作表：") else "# 工作表：数据"
        body = lines[1:] if lines[0].startswith("# 工作表：") else lines
        if not body:
            chunks.append(title)
            continue
        header = body[0]
        rows = body[1:] if len(body) > 1 else []
        if not rows:
            chunks.append(f"{title}\n{header}")
            continue

        buf_rows: list[str] = []
        for row in rows:
            candidate = buf_rows + [row]
            block = f"{title}\n{header}\n" + "\n".join(candidate)
            if buf_rows and (len(candidate) > rows_per_chunk or len(block) > max_chars):
                chunks.append(f"{title}\n{header}\n" + "\n".join(buf_rows))
                buf_rows = [row]
            else:
                buf_rows = candidate
        if buf_rows:
            chunks.append(f"{title}\n{header}\n" + "\n".join(buf_rows))
    return chunks


def detect_chunk_strategy(
    text: str,
    *,
    filename: str | None = None,
    category: str | None = None,
) -> str:
    """Return short | tabular | prose based on content morphology."""
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return "short"

    ext = Path(filename or "").suffix.lower()
    if ext in {".xlsx", ".csv"}:
        return "tabular"
    if ext in {".pptx"} or "# 幻灯片：" in stripped[:4000]:
        return "prose"
    if "# 工作表：" in stripped[:4000]:
        return "tabular"
    # Dense TSV/CSV-like blocks (many tab-separated lines).
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if len(lines) >= 4:
        tabby = sum(1 for ln in lines[:40] if ln.count("\t") >= 2 or ln.count(",") >= 3)
        if tabby / min(len(lines), 40) >= 0.5:
            return "tabular"

    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return "short"
    if category in {"invoice"} or len(stripped) <= SHORT_DOC_CHARS:
        return "short"
    return "prose"


def chunk_document(
    text: str,
    *,
    filename: str | None = None,
    category: str | None = None,
    chunk_size: int = 420,
    overlap: int = 80,
) -> list[str]:
    """Pick chunk strategy from file/content shape, then split."""
    strategy = detect_chunk_strategy(text, filename=filename, category=category)
    if strategy == "short":
        return chunk_short(text)
    if strategy == "tabular":
        return chunk_tabular(text)
    return chunk_text(text, chunk_size=chunk_size, overlap=overlap)


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


# Re-export for older imports / tests.
from corp_os.rag.embeddings import HashEmbedder, LocalEmbedder, get_embedder  # noqa: E402

__all__ = [
    "chunk_text",
    "chunk_short",
    "chunk_tabular",
    "chunk_document",
    "detect_chunk_strategy",
    "cosine",
    "dumps_embedding",
    "loads_embedding",
    "HashEmbedder",
    "LocalEmbedder",
    "get_embedder",
]
