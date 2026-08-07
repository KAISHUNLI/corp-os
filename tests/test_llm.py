from corp_os.rag.llm import build_template_answer, answer_with_rag, llm_enabled


def test_template_answer_empty():
    text = build_template_answer("随便问", [])
    assert "没有检索到" in text
    assert "补充上传" not in text
    assert "主管" in text or "人事" in text


def test_template_answer_empty_for_manager():
    text = build_template_answer("随便问", [], can_author_policy=True)
    assert "没有检索到" in text
    assert "上传到知识库" in text


def test_template_answer_with_hits():
    hits = [
        {
            "title": "员工考勤与纪律管理办法",
            "content": "当月迟到 5 次及以上：记过处分，取消季度奖金。",
            "score": 0.6,
        }
    ]
    text = build_template_answer("迟到五次有什么后果", hits)
    assert "考勤" in text
    assert "记过" in text


def test_answer_with_rag_falls_back_to_template_when_llm_off(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "off")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    assert llm_enabled() is False
    hits = [{"title": "差旅报销制度", "content": "需要发票和车票", "score": 0.5}]
    text = answer_with_rag("报销要什么", hits)
    assert "差旅报销制度" in text
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
