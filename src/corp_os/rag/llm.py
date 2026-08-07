"""Chat LLM via LangChain ChatOpenAI (OpenAI-compatible providers).

Build the model first with ``get_chat_model()``, then use it for RAG / intent /
agent (``bind_tools``). Raw HTTP is no longer used here.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

from corp_os.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是公司内部智能助手 corp-os。
规则：
1. 可根据「本会话文件」和「公司知识库」资料回答；会话文件优先用于用户刚发来的材料问答，未确认入库前不要当成已发布制度。
2. 资料不足就明确说不知道，不要编造条款。
3. 回答简洁、用中文，可分点；涉及处分/报销等给出对应条件。
4. 在相关处用《文档标题》点名依据；不要输出与问题无关的长文摘录。
5. 你看不到用户无权查看的资料；不要猜测隐藏制度。
6. 若资料内容是「未能抽取文字/未安装 OCR」之类占位说明，视为无效依据。
7. 若用户问「你能做什么/你是谁」这类元问题，不要用知识库条目回答，应说明助手能力。
8. 若有对话历史，要理解指代；聊天里用户自称不等于系统登录身份。
"""


def build_rag_system_prompt(user: Any | None = None) -> str:
    from corp_os.services.capabilities import policy_authoring_prompt_rules

    return SYSTEM_PROMPT.rstrip() + "\n9. " + policy_authoring_prompt_rules(user)


def llm_enabled() -> bool:
    settings = get_settings()
    provider = (settings.llm_provider or "off").strip().lower()
    if provider in {"off", "none", "template", ""}:
        return False
    if provider in {"openai", "openai_compatible", "api"}:
        return bool(settings.llm_api_base and settings.llm_model)
    return False


@lru_cache
def get_chat_model() -> ChatOpenAI:
    """Create the shared LangChain chat model (OpenAI-compatible endpoint)."""
    settings = get_settings()
    if not llm_enabled():
        raise RuntimeError("LLM 未启用")
    base = (settings.llm_api_base or "").rstrip("/")
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key or "EMPTY",
        base_url=base,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
    )


def build_template_answer(
    question: str,
    hits: list[dict],
    *,
    can_author_policy: bool = False,
) -> str:
    usable = [
        h
        for h in hits
        if h.get("content")
        and not str(h["content"]).strip().startswith("[已上传")
        and "未能自动抽取" not in str(h["content"])
        and "未识别到文字" not in str(h["content"])
    ]
    if not usable:
        if can_author_policy:
            return (
                "我在你有权限的公司资料里没有检索到足够相关的内容。\n"
                "你可以换个问法，或起草补充制度 Word 后确认「上传到知识库」。\n"
                "若问的是「我能做什么」，请直接说：你都能干啥。"
            )
        return (
            "我在你有权限的公司资料里没有检索到足够相关的内容。\n"
            "可以换个问法（例如：迟到、考勤、处分、报销），或联系主管/人事确认是否有相关制度。\n"
            "若问的是「我能做什么」，请直接说：你都能干啥。"
        )
    event_keywords = ("迟到", "早退", "旷工", "违规", "泄密", "请假")
    is_event = any(k in question for k in event_keywords)

    lines: list[str] = []
    if is_event:
        lines.append(f"针对「{question}」，根据公司制度中与你权限相关的条款，整理如下：")
    else:
        lines.append(f"关于「{question}」，我在公司知识库中找到这些依据：")
    lines.append("")
    for i, hit in enumerate(usable, start=1):
        lines.append(f"{i}. 来源：《{hit['title']}》")
        lines.append(hit["content"].strip())
        lines.append("")
    lines.append("说明：以上内容仅来自你可见的公司资料（RAG 检索结果）。")
    return "\n".join(lines).strip()


def _format_context(hits: list[dict]) -> str:
    blocks: list[str] = []
    for i, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{i}] 《{hit['title']}》（相关度 {hit.get('score', 0)}）\n{hit['content'].strip()}"
        )
    return "\n\n".join(blocks)


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def dict_messages_to_lc(messages: list[dict]) -> list[BaseMessage]:
    """Convert OpenAI-style message dicts to LangChain messages."""
    out: list[BaseMessage] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        text = content if isinstance(content, str) else ("" if content is None else str(content))
        if role == "system":
            out.append(SystemMessage(content=text))
        elif role == "user":
            out.append(HumanMessage(content=text))
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            lc_calls = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                lc_calls.append(
                    {
                        "id": tc.get("id") or str(fn.get("name") or "tool"),
                        "name": str(fn.get("name") or ""),
                        "args": _parse_tool_args(fn.get("arguments")),
                        "type": "tool_call",
                    }
                )
            if lc_calls:
                out.append(AIMessage(content=text, tool_calls=lc_calls))
            else:
                out.append(AIMessage(content=text))
        elif role == "tool":
            out.append(
                ToolMessage(
                    content=text,
                    tool_call_id=str(msg.get("tool_call_id") or ""),
                )
            )
    return out


def lc_message_to_dict(message: BaseMessage) -> dict[str, Any]:
    """Convert a LangChain AIMessage into OpenAI-style assistant dict."""
    if not isinstance(message, AIMessage):
        return {"role": "assistant", "content": getattr(message, "content", "") or ""}
    content = message.content
    if isinstance(content, list):
        # Rare multimodal content blocks — flatten text parts.
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        content = "".join(parts)
    out: dict[str, Any] = {"role": "assistant", "content": content or None}
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.get("id") or tc.get("name") or "tool",
                "type": "function",
                "function": {
                    "name": tc.get("name") or "",
                    "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
    return out


def chat_completion_turn(
    *,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = "auto",
) -> dict:
    """One chat turn via LangChain ChatOpenAI. Returns OpenAI-style assistant dict."""
    model: Any = get_chat_model()
    if tools:
        bind_kwargs: dict[str, Any] = {}
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = tool_choice
        model = model.bind_tools(tools, **bind_kwargs)
    result = model.invoke(dict_messages_to_lc(messages))
    return lc_message_to_dict(result)


def chat_completion(*, system: str, user: str, history: list[dict] | None = None) -> str:
    messages: list[dict] = [{"role": "system", "content": system}]
    for msg in history or []:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user})
    message = chat_completion_turn(
        messages=messages,
        tools=None,
        tool_choice=None,
    )
    return (message.get("content") or "").strip()


def answer_with_rag(
    question: str,
    hits: list[dict],
    *,
    history: list[dict] | None = None,
    user: Any | None = None,
) -> str:
    """Prefer LLM when configured; otherwise template. Never invent without hits."""
    from corp_os.services.governance import can_upload_company_knowledge

    can_author = bool(user is not None and can_upload_company_knowledge(user))
    usable = [
        h
        for h in hits
        if h.get("content")
        and not str(h["content"]).strip().startswith("[已上传")
        and "未能自动抽取" not in str(h["content"])
        and "未识别到文字" not in str(h["content"])
    ]
    if not usable:
        return build_template_answer(question, usable, can_author_policy=can_author)

    if not llm_enabled():
        return build_template_answer(question, usable, can_author_policy=can_author)

    user_prompt = (
        f"用户问题：{question}\n\n"
        f"可用资料（含本会话文件与公司知识库，仅可依据以下内容）：\n{_format_context(usable)}\n\n"
        "请结合对话历史（如有）作答。"
    )
    try:
        return chat_completion(
            system=build_rag_system_prompt(user),
            user=user_prompt,
            history=history,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM RAG answer failed, falling back to template: %s", exc)
        template = build_template_answer(question, usable, can_author_policy=can_author)
        return (
            f"{template}\n\n"
            f"（说明：大模型暂时不可用，已回退为摘录模式：{exc}）"
        )


@lru_cache
def get_llm_status() -> dict:
    settings = get_settings()
    return {
        "enabled": llm_enabled(),
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "api_base": settings.llm_api_base,
    }


def reset_llm_cache() -> None:
    get_llm_status.cache_clear()
    get_chat_model.cache_clear()
