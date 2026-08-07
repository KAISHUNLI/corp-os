from corp_os.rag.core import (
    LocalEmbedder,
    chunk_document,
    chunk_text,
    cosine,
    detect_chunk_strategy,
)
from corp_os.rag.embeddings import HashEmbedder, get_embedder, reset_embedder_cache
from corp_os.config import get_settings


def test_chunk_and_embed_similarity():
    chunks = chunk_text("迟到五次。\n\n当月迟到 5 次及以上：记过处分，取消季度奖金。")
    assert chunks
    emb = LocalEmbedder(dim=64)
    q = emb.embed_query("我迟到了五次有什么后果")
    d = emb.embed_documents(["当月迟到 5 次及以上：记过处分，取消季度奖金。"])[0]
    assert cosine(q, d) > cosine(q, emb.embed_documents(["差旅报销 7 个工作日"])[0])


def test_default_test_embedder_is_hash():
    get_settings.cache_clear()
    reset_embedder_cache()
    emb = get_embedder()
    assert isinstance(emb, HashEmbedder)


def test_short_doc_is_single_chunk():
    text = "火车票 OCR：北京南→上海虹桥 二等座 G123"
    assert detect_chunk_strategy(text, filename="ticket.png") == "short"
    assert chunk_document(text, filename="ticket.png") == [text]


def test_tabular_keeps_header_per_chunk():
    lines = ["# 工作表：库存", "品名\t数量\t仓"]
    for i in range(45):
        lines.append(f"螺丝{i}\t{i}\tA")
    text = "\n".join(lines)
    assert detect_chunk_strategy(text, filename="stock.xlsx") == "tabular"
    chunks = chunk_document(text, filename="stock.xlsx")
    assert len(chunks) >= 2
    assert all("品名\t数量\t仓" in c for c in chunks)
    assert all("# 工作表：库存" in c for c in chunks)


def test_policy_prose_uses_paragraph_chunker():
    paras = ["第%d条：本条规定内容。" % i + ("细则。" * 30) for i in range(6)]
    text = "\n\n".join(paras)
    assert detect_chunk_strategy(text, filename="handbook.docx", category="policy") == "prose"
    chunks = chunk_document(text, filename="handbook.docx", category="policy")
    assert len(chunks) >= 2
