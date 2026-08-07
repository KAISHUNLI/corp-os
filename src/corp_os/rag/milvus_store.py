"""Milvus vector store for corp-os chunk embeddings."""

from __future__ import annotations

import logging
from functools import lru_cache
from urllib.parse import urlparse

from corp_os.config import get_settings

logger = logging.getLogger(__name__)


def _parse_uri(uri: str) -> tuple[str, int]:
    raw = (uri or "").strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 19530
    return host, port


@lru_cache
def _connected_alias() -> str:
    settings = get_settings()
    from pymilvus import connections

    host, port = _parse_uri(settings.milvus_uri)
    alias = "corp_os"
    # Reconnect safely if alias exists from a previous settings object.
    try:
        connections.disconnect(alias)
    except Exception:  # noqa: BLE001
        pass
    connections.connect(alias=alias, host=host, port=port)
    logger.info("Connected to Milvus %s:%s", host, port)
    return alias


def reset_milvus_connection() -> None:
    _connected_alias.cache_clear()
    try:
        from pymilvus import connections

        connections.disconnect("corp_os")
    except Exception:  # noqa: BLE001
        pass


def ensure_collection(*, dim: int) -> str:
    """Create collection + index if missing. Returns collection name."""
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        utility,
    )

    settings = get_settings()
    alias = _connected_alias()
    name = settings.milvus_collection

    if utility.has_collection(name, using=alias):
        col = Collection(name, using=alias)
        # Dimension mismatch → drop and recreate (dev-friendly).
        for field in col.schema.fields:
            if field.name == "embedding" and field.params.get("dim") != dim:
                logger.warning(
                    "Milvus collection %s dim mismatch (%s != %s), recreating",
                    name,
                    field.params.get("dim"),
                    dim,
                )
                utility.drop_collection(name, using=alias)
                break
        else:
            col.load()
            return name

    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.INT64, is_primary=True, auto_id=False),
        FieldSchema(name="document_id", dtype=DataType.INT64),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    schema = CollectionSchema(fields, description="corp-os RAG chunks")
    col = Collection(name, schema=schema, using=alias)
    col.create_index(
        field_name="embedding",
        index_params={
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 128},
        },
    )
    col.load()
    logger.info("Created Milvus collection %s dim=%s", name, dim)
    return name


def _collection(dim: int) -> "Collection":
    from pymilvus import Collection

    name = ensure_collection(dim=dim)
    return Collection(name, using=_connected_alias())


def delete_by_document_ids(document_ids: list[int], *, dim: int) -> None:
    if not document_ids:
        return
    col = _collection(dim)
    # Milvus expr: document_id in [1, 2, 3]
    ids = ",".join(str(int(i)) for i in document_ids)
    col.delete(expr=f"document_id in [{ids}]")
    col.flush()


def delete_by_chunk_ids(chunk_ids: list[int], *, dim: int) -> None:
    if not chunk_ids:
        return
    col = _collection(dim)
    ids = ",".join(str(int(i)) for i in chunk_ids)
    col.delete(expr=f"chunk_id in [{ids}]")
    col.flush()


def upsert_chunks(
    *,
    chunk_ids: list[int],
    document_ids: list[int],
    embeddings: list[list[float]],
    dim: int,
) -> None:
    if not chunk_ids:
        return
    if not (len(chunk_ids) == len(document_ids) == len(embeddings)):
        raise ValueError("Milvus upsert length mismatch")
    col = _collection(dim)
    # Replace existing chunk ids then insert (portable across Milvus versions).
    delete_by_chunk_ids(chunk_ids, dim=dim)
    col.insert([chunk_ids, document_ids, embeddings])
    col.flush()


def search(
    query_vector: list[float],
    *,
    dim: int,
    top_k: int = 20,
    min_score: float = 0.0,
) -> list[tuple[int, int, float]]:
    """Return list of (chunk_id, document_id, score)."""
    col = _collection(dim)
    col.load()
    results = col.search(
        data=[query_vector],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=max(top_k, 1),
        output_fields=["document_id"],
    )
    out: list[tuple[int, int, float]] = []
    for hits in results:
        for hit in hits:
            score = float(hit.score)
            if score < min_score:
                continue
            chunk_id = int(hit.id)
            document_id = int(hit.entity.get("document_id"))
            out.append((chunk_id, document_id, score))
    return out
