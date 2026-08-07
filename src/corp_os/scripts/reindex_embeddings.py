"""Re-embed all active documents with the current embedding + vector store.

Usage (repo root):

  HF_ENDPOINT=https://hf-mirror.com \\
  PYTHONPATH=src .venv/bin/python -m corp_os.scripts.reindex_embeddings
"""

from __future__ import annotations

from corp_os.config import get_settings
from corp_os.db import SessionLocal
from corp_os.rag.embeddings import get_embedder
from corp_os.rag.store import reindex_active_documents


def main() -> None:
    settings = get_settings()
    embedder = get_embedder()
    print(
        f"provider={settings.embedding_provider} model={settings.embedding_model} "
        f"vector_store={settings.vector_store} embedder={type(embedder).__name__}"
    )
    db = SessionLocal()
    try:
        stats = reindex_active_documents(db)
        print(
            f"reindexed documents={stats['documents']} chunks={stats['chunks']} "
            f"vector_store={stats.get('vector_store')}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
