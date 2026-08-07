"""LangGraph orchestration for corp-os chat.

Flow (LLM on / agent_mode=auto|agent):
  classify → agent (tool-calling loop) → end

Flow (LLM off / agent_mode=rules):
  classify → kind_correct | governance_* | expense | erp_* | rag | smalltalk → end
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.models.iam import User
from corp_os.models.rag import ChatAttachment, ChatSession
from corp_os.rag.agent import run_tool_agent, use_agent_mode
from corp_os.rag.intent import intent_llm_enabled, resolve_route_with_intent
from corp_os.rag.llm import answer_with_rag, chat_completion, llm_enabled
from corp_os.rag.store import retrieve
from corp_os.services.erp_client import run_erp_tool
from corp_os.services.expense_check import check_expense, is_expense_intent, kind_label, normalize_kind
from corp_os.services.governance import decide_change_request, list_pending_for_approver
from corp_os.services.capabilities import (
    build_help_answer,
    build_identity_answer,
    is_help_intent,
    is_identity_intent,
)
from corp_os.config import get_settings
from corp_os.services.session_files import (
    is_library_publish_intent,
    publish_staged_to_library,
    session_file_hits,
)

RouteName = Literal[
    "agent",
    "kind_correct",
    "library_publish",
    "governance_decide",
    "governance_pending",
    "expense",
    "help",
    "identity",
    "smalltalk",
    "clarify",
    "erp_health",
    "erp_employees",
    "erp_employee_create",
    "erp_employee_delete",
    "erp_inventory",
    "erp_product_create",
    "erp_stock_in_help",
    "rag",
]


class ChatState(TypedDict, total=False):
    message: str
    history: list[dict[str, Any]]
    route: RouteName
    decide_id: int
    decide_action: str  # approve | reject
    intent: str
    intent_confidence: float
    answer: str
    citations: list[dict[str, Any]]
    action: str


_KIND_RE = re.compile(
    r"(?:这是|改成|纠正为|类型是)\s*(发票|车票|火车票|机票|审批单|出差审批|行程说明|制度|通知|其他)"
)
_DECIDE_RE = re.compile(r"(批准|同意|驳回|拒绝)\s*#?\s*(\d+)")
_SMALLTALK_RE = re.compile(
    r"^(你好|您好|嗨|hi|hello|hey|在吗|早上好|中午好|下午好|晚上好)[\s!！。.?？]*$",
    re.IGNORECASE,
)
# Broad ERP intents: "多少员工 / 有几个员工 / 公司几个人" etc.
_ERP_EMPLOYEES_RE = re.compile(
    r"(员工|职员|花名册|在职).{0,10}(多少|几个|人数|名单|列表|有哪些|查一下|查一查|查询)|"
    r"(多少|几个|有多少|一共多少|共有多少).{0,8}(员工|职员|人)|"
    r"(花名册|员工名单|员工列表|查员工|人员名单|在职人数|公司人数)"
)
_ERP_INVENTORY_RE = re.compile(
    r"(库存|仓库|结存|缺货).{0,10}(多少|几个|查|余额|还有)|"
    r"(还有多少货|查一下库存|仓库余额|库存结存)"
)
_CREATE_EMP_RE = re.compile(
    r"(?:入职|新增员工|添加员工|创建员工)\s*[：:\s]*([^\s，,。！!？?]+)"
)
_DELETE_EMP_RE = re.compile(
    r"(?:删除员工|办理离职|员工离职)\s*[#＃]?\s*(\d+)|(?:把员工|#)\s*(\d+)\s*(?:删掉|删除|离职)"
)
_CREATE_PRODUCT_RE = re.compile(
    r"(?:新建产品|新增产品|创建产品)\s*[：:\s]*([^\s，,。！!？?]+)"
)


def is_docgen_intent(text: str) -> bool:
    """User wants Word / PPT / Markdown generated (optionally from uploaded template)."""
    t = (text or "").strip()
    if not t:
        return False
    wants_gen = any(
        k in t
        for k in ("生成", "制作", "做一份", "做个", "写一份", "出一份", "弄一份", "导出")
    )
    kind = any(
        k in t
        for k in (
            "PPT",
            "ppt",
            "pptx",
            "幻灯片",
            "演示文稿",
            "Word",
            "word",
            "docx",
            "Markdown",
            "markdown",
            ".md",
        )
    )
    from_template = any(k in t for k in ("模板", "模版", "template")) and any(
        k in t for k in ("PPT", "ppt", "pptx", "幻灯片", "演示", "生成", "做")
    )
    return (wants_gen and kind) or from_template


def classify_route(message: str) -> tuple[RouteName, int | None, str | None]:
    """Rule router used when agent mode is off (LLM unavailable or agent_mode=rules)."""
    text = (message or "").strip()
    correct = _KIND_RE.search(text)
    if correct:
        return "kind_correct", None, None

    if is_library_publish_intent(text):
        return "library_publish", None, None

    # Doc generation needs the tool agent; mark as rag for rules fallback labeling,
    # but classify() will force agent when LLM is on.
    if is_docgen_intent(text):
        return "rag", None, None

    decide = _DECIDE_RE.search(text)
    if decide:
        decision = "approve" if decide.group(1) in {"批准", "同意"} else "reject"
        return "governance_decide", int(decide.group(2)), decision

    if any(k in text for k in ("待审批", "待我审批", "审批列表")):
        return "governance_pending", None, None

    if is_expense_intent(text):
        return "expense", None, None

    if is_identity_intent(text):
        return "identity", None, None

    if is_help_intent(text):
        return "help", None, None

    if any(k in text for k in ("erp通不通", "erp状态", "erp健康", "ERP通不通", "ERP状态", "ERP健康")):
        return "erp_health", None, None

    if _CREATE_EMP_RE.search(text):
        return "erp_employee_create", None, None

    if _DELETE_EMP_RE.search(text):
        return "erp_employee_delete", None, None

    if _CREATE_PRODUCT_RE.search(text):
        return "erp_product_create", None, None

    # ERP 库存入库：避免与「上传到知识库」冲突；裸「入库」不再默认走 ERP。
    if any(k in text for k in ("进货", "确认入库", "仓库入库", "库存入库")) or (
        "入库" in text and any(k in text for k in ("仓库", "产品", "数量", "stock"))
    ):
        return "erp_stock_in_help", None, None

    if _ERP_INVENTORY_RE.search(text) or any(k in text for k in ("库存", "仓库余额", "缺货", "结存", "还有多少货")):
        return "erp_inventory", None, None

    if _ERP_EMPLOYEES_RE.search(text):
        return "erp_employees", None, None

    if _SMALLTALK_RE.match(text):
        return "smalltalk", None, None

    return "rag", None, None


def build_chat_graph(db: Session, user: User, session: ChatSession):
    """Compile a per-request graph closed over db/user/session."""

    def classify(state: ChatState) -> ChatState:
        message = state["message"]
        history = list(state.get("history") or [])
        rule_route, decide_id, decide_action = classify_route(message)

        # Force full tool-agent only when explicitly requested.
        agent_mode = (get_settings().agent_mode or "auto").strip().lower()
        if agent_mode in {"agent", "tools", "tool"} and llm_enabled():
            if is_identity_intent(message) or rule_route == "identity":
                return {"route": "identity"}
            if is_help_intent(message) or rule_route == "help":
                return {"route": "help"}
            return {"route": "agent"}

        # 生成 Word/PPT/Markdown（含「根据模板生成」）必须走 tool-agent，避免 RAG 空话。
        if is_docgen_intent(message) and use_agent_mode():
            return {"route": "agent"}

        # Intent LLM (when enabled): classify first; low-conf falls back to rules.
        if intent_llm_enabled():
            route, did, dact, intent = resolve_route_with_intent(
                message,
                rule_route=rule_route,
                rule_decide_id=decide_id,
                rule_decide_action=decide_action,
                history=history,
            )
            # 通用问答走 tool-agent（可读会话文件 / 查库），不再卡在 clarify。
            if use_agent_mode() and route in {"rag", "clarify"}:
                route = "agent"
            out: ChatState = {"route": route}  # type: ignore[assignment]
            if did is not None:
                out["decide_id"] = did
            if dact is not None:
                out["decide_action"] = dact
            if intent is not None:
                out["intent"] = intent.intent
                out["intent_confidence"] = intent.confidence
            return out

        # No intent LLM: keep help/identity short-circuit; optional legacy agent; else rules.
        if is_identity_intent(message) or rule_route == "identity":
            return {"route": "identity"}
        if is_help_intent(message) or rule_route == "help":
            return {"route": "help"}
        if use_agent_mode():
            return {"route": "agent"}

        out = {"route": rule_route}
        if decide_id is not None:
            out["decide_id"] = decide_id
        if decide_action is not None:
            out["decide_action"] = decide_action
        return out

    def node_agent(state: ChatState) -> ChatState:
        result = run_tool_agent(
            db,
            user=user,
            session=session,
            message=state["message"],
            history=list(state.get("history") or []),
        )
        return {
            "answer": result.answer,
            "citations": result.citations,
            "action": result.action,
        }

    def node_kind_correct(state: ChatState) -> ChatState:
        match = _KIND_RE.search(state["message"])
        assert match is not None
        new_kind = normalize_kind(match.group(1))
        last = db.scalars(
            select(ChatAttachment)
            .where(ChatAttachment.session_id == session.id)
            .order_by(ChatAttachment.id.desc())
        ).first()
        if not last:
            answer = "当前对话还没有上传材料，请先点 + 上传文件。"
        else:
            old = last.kind
            last.kind = new_kind
            answer = (
                f"已把《{last.label}》类型从「{kind_label(old)}」改为「{kind_label(new_kind)}」。"
                "可继续问：这些够不够报销？"
            )
        return {"answer": answer, "citations": [], "action": "chat.kind_correct"}

    def node_library_publish(_state: ChatState) -> ChatState:
        answer = publish_staged_to_library(db, user=user, session_id=session.id)
        return {"answer": answer, "citations": [], "action": "chat.library_publish"}

    def node_governance_decide(state: ChatState) -> ChatState:
        decision = state.get("decide_action") or "reject"
        req_id = int(state.get("decide_id") or 0)
        try:
            req = decide_change_request(db, user=user, request_id=req_id, decision=decision)
            answer = (
                f"已处理审批单 #{req.id}：{decision}。\n"
                f"文件：《{req.title}》 action={req.action} status={req.status}"
            )
        except (PermissionError, ValueError) as exc:
            answer = f"无法处理审批：{exc}"
        return {"answer": answer, "citations": [], "action": "chat.governance_decide"}

    def node_governance_pending(_state: ChatState) -> ChatState:
        rows = list_pending_for_approver(db, user)
        if not rows:
            answer = "当前没有待你审批的文件变更。"
        else:
            lines = ["待你审批的重要文件变更："]
            for r in rows:
                lines.append(
                    f"- #{r.id} [{r.sensitivity}/{r.action}] 《{r.title}》 "
                    f"申请人 {r.requested_by}（回复：批准 #{r.id} / 驳回 #{r.id}）"
                )
            answer = "\n".join(lines)
        return {"answer": answer, "citations": [], "action": "chat.governance_pending"}

    def node_expense(state: ChatState) -> ChatState:
        result = check_expense(db, user=user, session_id=session.id, message=state["message"])
        return {
            "answer": result["answer"],
            "citations": result["citations"],
            "action": "chat.expense_check",
        }

    def node_help(_state: ChatState) -> ChatState:
        return {
            "answer": build_help_answer(user, db),
            "citations": [],
            "action": "chat.help",
        }

    def node_identity(state: ChatState) -> ChatState:
        return {
            "answer": build_identity_answer(user, history=list(state.get("history") or [])),
            "citations": [],
            "action": "chat.identity",
        }

    def node_clarify(state: ChatState) -> ChatState:
        answer = (
            "可以说具体一点，例如直接问制度条款、查员工/库存，"
            "或先点 + 发文件再问「帮我看看」/「上传到知识库」。"
        )
        return {"answer": answer, "citations": [], "action": "chat.clarify"}

    def node_smalltalk(state: ChatState) -> ChatState:
        history = list(state.get("history") or [])
        name = user.display_name or user.username
        if llm_enabled():
            try:
                answer = chat_completion(
                    system=(
                        "你是公司智能助手 corp-os。用户在闲聊或自我介绍。"
                        "简短友好地回应，记住对话里用户说过的话；"
                        "不要编造公司制度；不要假装已切换登录身份。"
                    ),
                    user=state["message"],
                    history=history,
                )
                if answer:
                    return {"answer": answer, "citations": [], "action": "chat.smalltalk"}
            except Exception:  # noqa: BLE001
                pass
        answer = (
            f"你好，{name}。我是公司智能助手。\n"
            f"{build_help_answer(user, db)}"
        )
        return {"answer": answer, "citations": [], "action": "chat.smalltalk"}

    def node_erp_health(_state: ChatState) -> ChatState:
        answer = run_erp_tool("health", user=user, db=db)
        return {"answer": answer, "citations": [], "action": "chat.erp_health"}

    def node_erp_employees(_state: ChatState) -> ChatState:
        answer = run_erp_tool("employees", user=user, db=db)
        return {"answer": answer, "citations": [], "action": "chat.erp_employees"}

    def node_erp_employee_create(state: ChatState) -> ChatState:
        match = _CREATE_EMP_RE.search(state["message"])
        name = match.group(1) if match else ""
        if not name:
            answer = "请说明姓名，例如：入职 王五"
        else:
            answer = run_erp_tool("employee_create", user=user, db=db, name=name)
        return {"answer": answer, "citations": [], "action": "chat.erp_employee_create"}

    def node_erp_employee_delete(state: ChatState) -> ChatState:
        match = _DELETE_EMP_RE.search(state["message"])
        emp_id = None
        if match:
            emp_id = match.group(1) or match.group(2)
        if not emp_id:
            answer = "请给出员工 ID，例如：删除员工 #3"
        else:
            answer = run_erp_tool("employee_delete", user=user, db=db, employee_id=int(emp_id))
        return {"answer": answer, "citations": [], "action": "chat.erp_employee_delete"}

    def node_erp_inventory(_state: ChatState) -> ChatState:
        answer = run_erp_tool("inventory", user=user, db=db)
        return {"answer": answer, "citations": [], "action": "chat.erp_inventory"}

    def node_erp_product_create(state: ChatState) -> ChatState:
        match = _CREATE_PRODUCT_RE.search(state["message"])
        name = match.group(1) if match else ""
        if not name:
            answer = "请说明产品名，例如：新建产品 螺丝"
        else:
            answer = run_erp_tool("product_create", user=user, db=db, name=name)
        return {"answer": answer, "citations": [], "action": "chat.erp_product_create"}

    def node_erp_stock_in_help(_state: ChatState) -> ChatState:
        answer = (
            "入库需要仓库 ID、产品 ID、数量。可先说「查一下库存」或让 Agent（开启 LLM）执行：\n"
            "stock_in(warehouse_id=…, product_id=…, product_name=…, qty=…)\n"
            "也可分步：新建仓库 / 新建产品，再入库。"
        )
        return {"answer": answer, "citations": [], "action": "chat.erp_stock_in_help"}

    def node_rag(state: ChatState) -> ChatState:
        # Prefer answering from files just sent in this session; then company KB.
        file_hits = session_file_hits(db, session.id)
        kb_hits = retrieve(db, user=user, query=state["message"], top_k=5)
        hits = file_hits + kb_hits
        answer = answer_with_rag(
            state["message"],
            hits,
            history=list(state.get("history") or []),
            user=user,
        )
        citations = [
            {
                "document_id": h["document_id"],
                "title": h["title"],
                "category": h["category"],
                "snippet": h["content"][:180],
                "score": h["score"],
            }
            for h in hits
        ]
        return {"answer": answer, "citations": citations, "action": "chat.rag"}

    def pick_route(state: ChatState) -> RouteName:
        return state["route"]

    graph = StateGraph(ChatState)
    graph.add_node("classify", classify)
    graph.add_node("agent", node_agent)
    graph.add_node("kind_correct", node_kind_correct)
    graph.add_node("library_publish", node_library_publish)
    graph.add_node("governance_decide", node_governance_decide)
    graph.add_node("governance_pending", node_governance_pending)
    graph.add_node("expense", node_expense)
    graph.add_node("help", node_help)
    graph.add_node("identity", node_identity)
    graph.add_node("clarify", node_clarify)
    graph.add_node("smalltalk", node_smalltalk)
    graph.add_node("erp_health", node_erp_health)
    graph.add_node("erp_employees", node_erp_employees)
    graph.add_node("erp_employee_create", node_erp_employee_create)
    graph.add_node("erp_employee_delete", node_erp_employee_delete)
    graph.add_node("erp_inventory", node_erp_inventory)
    graph.add_node("erp_product_create", node_erp_product_create)
    graph.add_node("erp_stock_in_help", node_erp_stock_in_help)
    graph.add_node("rag", node_rag)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        pick_route,
        {
            "agent": "agent",
            "kind_correct": "kind_correct",
            "library_publish": "library_publish",
            "governance_decide": "governance_decide",
            "governance_pending": "governance_pending",
            "expense": "expense",
            "help": "help",
            "identity": "identity",
            "clarify": "clarify",
            "smalltalk": "smalltalk",
            "erp_health": "erp_health",
            "erp_employees": "erp_employees",
            "erp_employee_create": "erp_employee_create",
            "erp_employee_delete": "erp_employee_delete",
            "erp_inventory": "erp_inventory",
            "erp_product_create": "erp_product_create",
            "erp_stock_in_help": "erp_stock_in_help",
            "rag": "rag",
        },
    )
    for name in (
        "agent",
        "kind_correct",
        "library_publish",
        "governance_decide",
        "governance_pending",
        "expense",
        "help",
        "identity",
        "clarify",
        "smalltalk",
        "erp_health",
        "erp_employees",
        "erp_employee_create",
        "erp_employee_delete",
        "erp_inventory",
        "erp_product_create",
        "erp_stock_in_help",
        "rag",
    ):
        graph.add_edge(name, END)

    return graph.compile()


def run_chat_graph(
    db: Session,
    *,
    user: User,
    session: ChatSession,
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> ChatState:
    app = build_chat_graph(db, user, session)
    return app.invoke({"message": message, "history": list(history or [])})
