from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.models.rag import DocumentChunk
from corp_os.rag.core import chunk_document, cosine, dumps_embedding, loads_embedding
from corp_os.rag.embeddings import get_embedder
from corp_os.services.permissions import can_view_document


def _vector_backend() -> str:
    return (get_settings().vector_store or "postgres").strip().lower()


def _vector_dim(sample: list[float] | None = None) -> int:
    settings = get_settings()
    if sample:
        return len(sample)
    # Prefer configured dim for milvus collection; hash uses embedding_dim.
    provider = (settings.embedding_provider or "").lower()
    if provider in {"hash", "local"}:
        return int(settings.embedding_dim)
    return int(settings.embedding_vector_dim)


def index_document(db: Session, doc: Document) -> int:
    """Chunk + embed a document. PG keeps text; vectors go to configured backend."""
    text = (doc.full_text or doc.text_excerpt or "").strip()

    # Drop old vectors for this document before replacing chunks.
    if _vector_backend() == "milvus":
        from corp_os.rag import milvus_store

        try:
            milvus_store.delete_by_document_ids([doc.id], dim=_vector_dim())
        except Exception:
            # Collection may not exist yet; ensure on upsert.
            pass

    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    if not text:
        db.flush()
        return 0

    embedder = get_embedder()
    pieces = chunk_document(text, filename=doc.filename, category=doc.category)
    vectors = embedder.embed_documents(pieces)
    dim = _vector_dim(vectors[0] if vectors else None)
    backend = _vector_backend()

    chunk_rows: list[DocumentChunk] = []
    for i, (piece, vec) in enumerate(zip(pieces, vectors, strict=True)):
        # Keep embedding_json when using postgres backend; milvus keeps [] to avoid huge PG rows.
        emb_json = dumps_embedding(vec) if backend != "milvus" else "[]"
        row = DocumentChunk(
            document_id=doc.id,
            chunk_index=i,
            content=piece,
            embedding_json=emb_json,
            token_count=len(piece),
        )
        db.add(row)
        chunk_rows.append(row)
    db.flush()

    if backend == "milvus":
        from corp_os.rag import milvus_store

        milvus_store.upsert_chunks(
            chunk_ids=[c.id for c in chunk_rows],
            document_ids=[doc.id] * len(chunk_rows),
            embeddings=vectors,
            dim=dim,
        )

    return len(pieces)


def reindex_active_documents(db: Session) -> dict[str, int]:
    """Re-chunk + re-embed all active knowledge docs (after switching embedder/store)."""
    docs = list(db.scalars(select(Document).where(Document.status == "active")))
    total_chunks = 0
    for doc in docs:
        total_chunks += index_document(db, doc)
    db.commit()
    return {"documents": len(docs), "chunks": total_chunks, "vector_store": _vector_backend()}


def _retrieve_postgres(
    db: Session,
    *,
    user: User,
    query: str,
    top_k: int,
) -> list[dict]:
    settings = get_settings()
    embedder = get_embedder()
    q_vec = embedder.embed_query(query)
    chunks = list(db.scalars(select(DocumentChunk)))
    if not chunks:
        return []

    doc_ids = {c.document_id for c in chunks}
    docs = {
        d.id: d
        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids), Document.status == "active"))
    }

    min_score = float(settings.embedding_min_score)
    if (settings.embedding_provider or "").lower() in {"hash", "local"}:
        min_score = min(min_score, 0.05)

    scored: list[tuple[float, DocumentChunk, Document]] = []
    for chunk in chunks:
        doc = docs.get(chunk.document_id)
        if not doc or not can_view_document(user, doc):
            continue
        score = cosine(q_vec, loads_embedding(chunk.embedding_json))
        if score > min_score:
            scored.append((score, chunk, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [_hit(score, chunk, doc) for score, chunk, doc in scored[:top_k]]


def _retrieve_milvus(
    db: Session,
    *,
    user: User,
    query: str,
    top_k: int,
) -> list[dict]:
    from corp_os.rag import milvus_store

    settings = get_settings()
    embedder = get_embedder()
    q_vec = embedder.embed_query(query)
    dim = _vector_dim(q_vec)
    min_score = float(settings.embedding_min_score)

    # Over-fetch then ACL-filter in app (complex visibility rules stay in Python).
    candidates = milvus_store.search(
        q_vec,
        dim=dim,
        top_k=max(top_k * 10, 30),
        min_score=min_score,
    )
    if not candidates:
        return []

    chunk_ids = [c[0] for c in candidates]
    chunks = {
        c.id: c
        for c in db.scalars(select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids)))
    }
    doc_ids = {c.document_id for c in chunks.values()}
    docs = {
        d.id: d
        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids), Document.status == "active"))
    }

    results: list[dict] = []
    for chunk_id, _document_id, score in candidates:
        chunk = chunks.get(chunk_id)
        if not chunk:
            continue
        doc = docs.get(chunk.document_id)
        if not doc or not can_view_document(user, doc):
            continue
        results.append(_hit(score, chunk, doc))
        if len(results) >= top_k:
            break
    return results


def _hit(score: float, chunk: DocumentChunk, doc: Document) -> dict:
    return {
        "score": round(float(score), 4),
        "chunk_id": chunk.id,
        "document_id": doc.id,
        "title": doc.title or doc.filename,
        "category": doc.category,
        "content": chunk.content,
    }


def retrieve(
    db: Session,
    *,
    user: User,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    if _vector_backend() == "milvus":
        return _retrieve_milvus(db, user=user, query=query, top_k=top_k)
    return _retrieve_postgres(db, user=user, query=query, top_k=top_k)
