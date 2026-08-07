"""LLM intent classification: decide route before RAG.

Enterprise-style flow:
  hard rules → (optional) LLM intent JSON → map to route
  Only intent=knowledge goes to RAG; meta/chitchat never retrieve.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from corp_os.config import get_settings
from corp_os.rag.llm import chat_completion, llm_enabled

logger = logging.getLogger(__name__)

IntentName = str  # knowledge | meta | chitchat | expense | ...

INTENT_TO_ROUTE: dict[str, str] = {
    "knowledge": "rag",
    "meta": "help",
    "help": "help",
    "identity": "identity",
    "whoami": "identity",
    "chitchat": "smalltalk",
    "smalltalk": "smalltalk",
    "expense": "expense",
    "library_publish": "library_publish",
    "governance_pending": "governance_pending",
    "erp_health": "erp_health",
    "erp_employees": "erp_employees",
    "erp_employee_create": "erp_employee_create",
    "erp_employee_delete": "erp_employee_delete",
    "erp_inventory": "erp_inventory",
    "erp_product_create": "erp_product_create",
    "erp_stock_in": "erp_stock_in_help",
    # unclear 不直接 clarify：由 intent_to_route 回退规则路由
    "unclear": "rag",
}

INTENT_SYSTEM = """你是 corp-os 的意图分类器。只输出一个 JSON 对象，不要 Markdown，不要解释。

可选 intent（只能选一个）：
- knowledge：问公司制度/规章/政策；或解读/提问本会话刚发的文件；或一般业务问答需要查资料
- meta：问助手能做什么、你是谁（助手）、怎么用
- identity：问「我是谁/我的账号/我的角色」
- chitchat：寒暄打招呼；或用户自称身份（如「我是老板」）但未提问
- expense：报销材料是否齐全、缺什么票
- library_publish：确认把本会话暂存文件写入公司知识库（「上传到知识库」等）
- governance_pending：待我审批、审批列表
- erp_health：ERP 通不通
- erp_employees：查员工人数/名单
- erp_employee_create：入职/新增员工
- erp_employee_delete：删除/离职员工
- erp_inventory：查库存
- erp_product_create：新建产品
- erp_stock_in：仓库/库存入库（不是知识库）
- unclear：仅当消息几乎无意义（如单独「嗯」「？」）时使用；有实质内容时不要选 unclear

规则：
1. 「你都能干啥/有什么功能」→ meta
2. 「我是谁/我的角色」→ identity
3. 「你是谁」→ meta
4. 「迟到五次什么处分」「这份文件说了什么」「帮我看看」→ knowledge
5. 「我是老板」单独一句 → chitchat
6. 「上传到知识库」→ library_publish，绝不是 erp_stock_in
7. 「生成 PPT / Word / 根据模板做幻灯片」→ knowledge（系统会走工具代理生成文档）
8. 有疑问句或业务内容时优先 knowledge，不要轻易 unclear
9. confidence 为 0~1；拿不准可给 knowledge 并降低 confidence，系统会回退，不必选 unclear

输出格式严格为：
{"intent":"knowledge","confidence":0.86,"reason":"一句话原因"}
"""


@dataclass
class IntentResult:
    intent: str
    confidence: float
    reason: str = ""
    source: str = "llm"  # llm | rules | fallback


def intent_llm_enabled() -> bool:
    settings = get_settings()
    if not llm_enabled():
        return False
    flag = (settings.intent_llm_mode or "auto").strip().lower()
    if flag in {"off", "false", "0", "rules"}:
        return False
    if flag in {"on", "true", "1", "llm", "auto"}:
        return True
    return True


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def classify_intent_llm(
    message: str,
    *,
    history: list[dict[str, Any]] | None = None,
) -> IntentResult:
    """Call chat LLM to classify user intent. Raises on transport errors."""
    from corp_os.rag.memory import format_history_block

    hist = format_history_block(history)
    user_prompt = f"用户消息：{(message or '').strip()}"
    if hist:
        user_prompt = f"最近对话：\n{hist}\n\n当前用户消息：{(message or '').strip()}"
    content = chat_completion(
        system=INTENT_SYSTEM,
        user=user_prompt,
    )
    data = _extract_json(content) or {}
    intent = str(data.get("intent") or "knowledge").strip().lower()
    if intent not in INTENT_TO_ROUTE:
        intent = "knowledge"
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(data.get("reason") or "")[:200]
    return IntentResult(intent=intent, confidence=confidence, reason=reason, source="llm")


def intent_to_route(result: IntentResult, *, fallback_route: str = "rag") -> str:
    """Map intent → route. Low confidence / unclear → rules fallback（不弹 clarify）。"""
    settings = get_settings()
    min_conf = float(settings.intent_min_confidence or 0.35)
    mapped = INTENT_TO_ROUTE.get(result.intent, fallback_route)

    if result.intent == "unclear":
        return fallback_route if fallback_route != "clarify" else "rag"

    if result.confidence < min_conf:
        # 置信度不够：回退关键词规则，避免反复「我还不确定」
        return fallback_route if fallback_route != "clarify" else "rag"

    return mapped


def resolve_route_with_intent(
    message: str,
    *,
    rule_route: str,
    rule_decide_id: int | None,
    rule_decide_action: str | None,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, int | None, str | None, IntentResult | None]:
    """Combine hard rules + optional LLM intent.

    Hard structural routes always win.
    Low-confidence / unclear LLM → fall back to rule_route (usually rag), never clarify spam.
    """
    if rule_route in {"kind_correct", "governance_decide", "identity", "help", "library_publish"}:
        return rule_route, rule_decide_id, rule_decide_action, None

    if not intent_llm_enabled():
        return rule_route, rule_decide_id, rule_decide_action, None

    try:
        result = classify_intent_llm(message, history=history)
    except Exception as exc:  # noqa: BLE001
        logger.exception("intent LLM failed, fallback to rules: %s", exc)
        return rule_route, rule_decide_id, rule_decide_action, IntentResult(
            intent="knowledge",
            confidence=0.0,
            reason=f"intent llm error: {exc}",
            source="fallback",
        )

    # Precision ops from rules beat soft LLM labels.
    if rule_route not in {"rag", "smalltalk", "help", "clarify"} and result.intent in {
        "knowledge",
        "unclear",
        "meta",
        "chitchat",
    }:
        if rule_route.startswith("erp_") or rule_route in {"expense", "governance_pending"}:
            return rule_route, rule_decide_id, rule_decide_action, result

    route = intent_to_route(result, fallback_route=rule_route)
    if route == "clarify":
        route = rule_route if rule_route != "clarify" else "rag"

    if route in {"governance_decide"}:
        return route, rule_decide_id, rule_decide_action, result
    return route, None, None, result
