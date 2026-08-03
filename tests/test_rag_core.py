from corp_os.rag.core import chunk_text, LocalEmbedder, cosine


def test_chunk_and_embed_similarity():
    chunks = chunk_text("迟到五次。\n\n当月迟到 5 次及以上：记过处分，取消季度奖金。")
    assert chunks
    emb = LocalEmbedder(dim=64)
    q = emb.embed_query("我迟到了五次有什么后果")
    d = emb.embed_documents(["当月迟到 5 次及以上：记过处分，取消季度奖金。"])[0]
    assert cosine(q, d) > cosine(q, emb.embed_documents(["差旅报销 7 个工作日"])[0])
