from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from corp_os.models.document import Document
from corp_os.models.iam import User
from corp_os.models.rag import DocumentChunk
from corp_os.rag.core import LocalEmbedder, chunk_text, cosine, dumps_embedding, loads_embedding
from corp_os.services.permissions import can_view_document


_embedder = LocalEmbedder()


def index_document(db: Session, doc: Document) -> int:
    """Chunk + embed a document into the company RAG store."""
    text = (doc.full_text or doc.text_excerpt or "").strip()
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    if not text:
        db.flush()
        return 0

    pieces = chunk_text(text)
    vectors = _embedder.embed_documents(pieces)
    for i, (piece, vec) in enumerate(zip(pieces, vectors, strict=True)):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                chunk_index=i,
                content=piece,
                embedding_json=dumps_embedding(vec),
                token_count=len(piece),
            )
        )
    db.flush()
    return len(pieces)


def retrieve(
    db: Session,
    *,
    user: User,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    q_vec = _embedder.embed_query(query)
    chunks = list(db.scalars(select(DocumentChunk)))
    if not chunks:
        return []

    doc_ids = {c.document_id for c in chunks}
    docs = {
        d.id: d
        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids), Document.status == "active"))
    }

    scored: list[tuple[float, DocumentChunk, Document]] = []
    for chunk in chunks:
        doc = docs.get(chunk.document_id)
        if not doc or not can_view_document(user, doc):
            continue
        score = cosine(q_vec, loads_embedding(chunk.embedding_json))
        if score > 0.05:
            scored.append((score, chunk, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    results: list[dict] = []
    for score, chunk, doc in scored[:top_k]:
        results.append(
            {
                "score": round(float(score), 4),
                "chunk_id": chunk.id,
                "document_id": doc.id,
                "title": doc.title or doc.filename,
                "category": doc.category,
                "content": chunk.content,
            }
        )
    return results
