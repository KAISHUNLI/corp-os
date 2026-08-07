"""Tests for LLM intent classification gate."""

from unittest.mock import patch

from corp_os.rag.intent import (
    IntentResult,
    classify_intent_llm,
    intent_to_route,
    resolve_route_with_intent,
)


def test_intent_to_route_knowledge():
    assert intent_to_route(IntentResult("knowledge", 0.9)) == "rag"


def test_intent_to_route_meta_never_rag():
    assert intent_to_route(IntentResult("meta", 0.95)) == "help"


def test_intent_low_confidence_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("CORP_OS_INTENT_MIN_CONFIDENCE", "0.55")
    from corp_os.config import get_settings

    get_settings.cache_clear()
    assert intent_to_route(IntentResult("knowledge", 0.2), fallback_route="rag") == "rag"
    assert intent_to_route(IntentResult("unclear", 0.9), fallback_route="expense") == "expense"
    get_settings.cache_clear()


def test_intent_high_confidence_meta():
    assert intent_to_route(IntentResult("meta", 0.9), fallback_route="rag") == "help"

def test_classify_intent_llm_parses_json(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CORP_OS_LLM_API_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("CORP_OS_LLM_MODEL", "mock")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    with patch(
        "corp_os.rag.intent.chat_completion",
        return_value='{"intent":"meta","confidence":0.91,"reason":"问能力"}',
    ):
        result = classify_intent_llm("你都能干啥")
    assert result.intent == "meta"
    assert result.confidence >= 0.9
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()


def test_resolve_prefers_structural_rules(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CORP_OS_LLM_API_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("CORP_OS_INTENT_LLM_MODE", "on")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    route, did, dact, _ = resolve_route_with_intent(
        "批准 #9",
        rule_route="governance_decide",
        rule_decide_id=9,
        rule_decide_action="approve",
    )
    assert route == "governance_decide"
    assert did == 9
    assert dact == "approve"
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()


def test_resolve_llm_meta_overrides_default_rag(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CORP_OS_LLM_API_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("CORP_OS_INTENT_LLM_MODE", "on")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    with patch(
        "corp_os.rag.intent.classify_intent_llm",
        return_value=IntentResult("meta", 0.93, "能力", "llm"),
    ):
        route, _, _, intent = resolve_route_with_intent(
            "你都能干啥",
            rule_route="rag",
            rule_decide_id=None,
            rule_decide_action=None,
        )
    assert route == "help"
    assert intent and intent.intent == "meta"
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()


def test_resolve_identity_rule_wins_over_unclear_llm(monkeypatch):
    monkeypatch.setenv("CORP_OS_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("CORP_OS_LLM_API_BASE", "http://example.invalid/v1")
    monkeypatch.setenv("CORP_OS_INTENT_LLM_MODE", "on")
    from corp_os.config import get_settings
    from corp_os.rag import llm as llm_mod

    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
    with patch(
        "corp_os.rag.intent.classify_intent_llm",
        return_value=IntentResult("unclear", 0.6, "拿不准", "llm"),
    ):
        route, _, _, intent = resolve_route_with_intent(
            "我是谁",
            rule_route="identity",
            rule_decide_id=None,
            rule_decide_action=None,
        )
    assert route == "identity"
    assert intent is None
    get_settings.cache_clear()
    llm_mod.reset_llm_cache()
