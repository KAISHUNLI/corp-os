import os

# Unit tests stay offline and fast; real BGE/Milvus are for local/dev via .env.
os.environ.setdefault("CORP_OS_EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("CORP_OS_VECTOR_STORE", "postgres")
