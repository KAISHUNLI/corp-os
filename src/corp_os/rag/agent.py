"""Tool-calling agent loop (step 8).

Model is built via LangChain ``get_chat_model()``, then ``bind_tools`` for the
ReAct-style loop. Falls back to rule graph when LLM is off.
"""

from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.models.iam import User
from corp_os.models.rag import ChatSession
from corp_os.rag.llm import dict_messages_to_lc, get_chat_model, lc_message_to_dict, llm_enabled
from corp_os.rag.store import retrieve
from corp_os.services.erp_client import gateway_call, run_erp_tool
from corp_os.services.session_files import (
    publish_staged_to_library,
    resolve_pptx_template,
    session_file_hits,
)
from corp_os.services.docgen import (
    format_tool_result,
    generate_markdown,
    generate_powerpoint,
    generate_word,
)
from corp_os.services.permissions import can_use_tool, tool_denied_message

logger = logging.getLogger(__name__)

AGENT_SYSTEM = """你是公司内部智能助手 corp-os。通过工具完成任务，不要编造数据。

规则：
1. 用户刚发了文件但未确认入库：用 read_session_files 看内容并回答；不要当成已发布公司制度。
2. 用户明确说「上传到知识库」→ publish_to_knowledge_base（仅对应主管/老板能成功写入）。
3. 查已入库制度/规章 → search_company_knowledge。
4. 用户要生成 Word → generate_word；PPT/幻灯片 → generate_powerpoint；Markdown/md → generate_markdown。
   - 必须先调用对应工具；禁止自己编造下载地址或文件 ID。
   - 工具返回的「下载地址：/api/v1/chat/generated/xxx」必须原样复制给用户，一个字符都不要改。
   - PPT 优先套用公司模板视觉（只改占位文字）；失败则回退稳定版式，保证可下载。
4b. 用户要「发我/下载/看一下」公司 PPT 模板原文件 → share_library_file（返回 /api/v1/chat/library/ID）。
   公司内部模板允许有权限的同事下载；不要说无法提供文件。
5. 查员工/人数 → list_employees；看某人详情 → get_employee。
6. 入职/新增员工 → create_employee；改信息 → update_employee；删除/离职 → delete_employee。
7. 查库存 → list_inventory；查产品 → list_products；查仓库 → list_warehouses。
8. 新建产品/仓库 → create_product / create_warehouse；改/删产品 → update_product / delete_product。
9. 仓库库存入库 → stock_in（需 warehouse_id、product_id、product_name、qty）；与知识库无关。
10. ERP 是否通 → check_erp_health。
10b. ERP 全站能力（销售/采购/财务/HR/CRM/制造/分析等）：先 erp_find_operations 搜接口，再 erp_call 调用。
    - path 只填相对路径（如 /sales/orders），不要填完整 URL。
    - 写操作缺 ID 时先 GET 列表/详情查清；不要猜测 ID。
    - 工具返回无权/失败就如实转告。
11. 写操作前若缺 ID，先 list/get 查清再改删；不要猜测 ID。
12. 工具返回「无权」就如实转告，不要绕过。
13. 用中文简洁回答。
14. 结合对话历史理解指代；用户聊天自称不等于登录权限。
"""


def build_agent_system_prompt(user: User) -> str:
    from corp_os.services.capabilities import policy_authoring_prompt_rules

    return AGENT_SYSTEM.rstrip() + "\n15. " + policy_authoring_prompt_rules(user)

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_company_knowledge",
            "description": "检索公司知识库（已入库制度、通知、规章）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_session_files",
            "description": "读取本对话暂存/附件文件内容，用于根据刚发的图片或文档提问（尚未入库）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "share_library_file",
            "description": (
                "提供公司知识库或本会话中可下载原文件的下载地址（如公司 PPT 模板）。"
                "用户说「发我模板/下载商务汇报.pptx」时必须调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "integer",
                        "description": "已知文档 ID 时传入",
                    },
                    "query": {
                        "type": "string",
                        "description": "按标题/文件名查找，如「商务汇报」「PPT模板」",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_to_knowledge_base",
            "description": "把本对话暂存文件写入公司知识库（需用户明确确认；对应主管/老板权限）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "integer",
                        "description": "可选，指定单个文档；省略则处理全部暂存文件",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_word",
            "description": "根据标题和章节内容生成 Word（.docx）文档，返回下载地址。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "body": {
                        "type": "string",
                        "description": "可选：整篇正文（无章节结构时用）",
                    },
                    "sections": {
                        "type": "array",
                        "description": "章节列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "paragraphs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_powerpoint",
            "description": (
                "生成可下载的 PPT（.pptx）。优先套用公司/会话 PPTX 模板视觉（只改占位文字，不删页）；"
                "套用失败则回退稳定版式，保证可预览/下载。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "演示文稿标题"},
                    "slides": {
                        "type": "array",
                        "description": "幻灯片列表（可按模板页数组织）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["title"],
                        },
                    },
                    "use_session_template": {
                        "type": "boolean",
                        "description": "true=优先套用会话/知识库 PPTX 模板视觉（默认 true）；失败会自动回退",
                    },
                    "use_company_template": {
                        "type": "boolean",
                        "description": "true=优先使用公司知识库里的 PPTX 模板",
                    },
                    "template_document_id": {
                        "type": "integer",
                        "description": "可选，指定模板文档 ID（会话附件或知识库）",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_markdown",
            "description": "根据标题和章节/正文生成 Markdown（.md）文件，返回下载地址。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "body": {
                        "type": "string",
                        "description": "可选：完整 Markdown 正文（可含 # 标题）",
                    },
                    "sections": {
                        "type": "array",
                        "description": "章节列表（与 body 二选一或并用）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "paragraphs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_employees",
            "description": "查询 ERP 员工列表/人数。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "可选姓名关键词"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_employee",
            "description": "按员工 ID 查看 ERP 员工详情。",
            "parameters": {
                "type": "object",
                "properties": {"employee_id": {"type": "integer"}},
                "required": ["employee_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_employee",
            "description": "在 ERP 新增大员/入职。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "department": {"type": "string"},
                    "title": {"type": "string"},
                    "phone": {"type": "string"},
                    "email": {"type": "string"},
                    "remark": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_employee",
            "description": "更新 ERP 员工信息（姓名/部门/状态等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "department": {"type": "string"},
                    "title": {"type": "string"},
                    "phone": {"type": "string"},
                    "email": {"type": "string"},
                    "status": {"type": "string", "description": "active 或 resigned"},
                    "remark": {"type": "string"},
                },
                "required": ["employee_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_employee",
            "description": "删除员工；若 ERP 不支持删除则标记离职。",
            "parameters": {
                "type": "object",
                "properties": {"employee_id": {"type": "integer"}},
                "required": ["employee_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_inventory",
            "description": "查询库存结存。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_products",
            "description": "查询产品列表。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_warehouses",
            "description": "查询仓库列表。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_product",
            "description": "新建产品。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "unit": {"type": "string"},
                    "spec": {"type": "string"},
                    "sale_price": {"type": "number"},
                    "remark": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_product",
            "description": "更新产品。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "unit": {"type": "string"},
                    "spec": {"type": "string"},
                    "sale_price": {"type": "number"},
                    "status": {"type": "string"},
                    "remark": {"type": "string"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_product",
            "description": "删除产品；不支持时停用。",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_warehouse",
            "description": "新建仓库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                    "remark": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_in",
            "description": "创建并确认入库单，增加库存。",
            "parameters": {
                "type": "object",
                "properties": {
                    "warehouse_id": {"type": "integer"},
                    "product_id": {"type": "integer"},
                    "product_name": {"type": "string"},
                    "qty": {"type": "number"},
                    "unit": {"type": "string"},
                    "remark": {"type": "string"},
                },
                "required": ["warehouse_id", "product_id", "product_name", "qty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_erp_health",
            "description": "检查 ERP 是否连通。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "erp_find_operations",
            "description": (
                "按关键词检索 company-er（ERP）可用 API（来自 OpenAPI）。"
                "覆盖销售/采购/库存/财务/HR/CRM/制造/分析/系统等。"
                "先用本工具找到 method+path，再用 erp_call 调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言关键词，如「销售订单」「请假」「毛利率」",
                    },
                    "tag": {
                        "type": "string",
                        "description": "可选，OpenAPI 中文 tag，如「销售订单」「员工」",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 12，最大 30",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "erp_call",
            "description": (
                "调用 company-er ERP 任意 API。path 为相对路径（如 /sales/orders 或 /api/v1/sales/orders）。"
                "GET 用 params；POST/PUT/PATCH 用 body（JSON 对象）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "GET / POST / PUT / PATCH / DELETE",
                    },
                    "path": {
                        "type": "string",
                        "description": "相对路径，例如 /hr/leave-requests",
                    },
                    "params": {
                        "type": "object",
                        "description": "查询参数（page、keyword 等）",
                    },
                    "body": {
                        "type": "object",
                        "description": "JSON 请求体（写操作）",
                    },
                },
                "required": ["method", "path"],
            },
        },
    },
]


@dataclass
class AgentResult:
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    action: str = "chat.agent"
    tool_trace: list[str] = field(default_factory=list)


@dataclass
class _ToolContext:
    db: Session
    user: User
    session: ChatSession
    message: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    generated_urls: list[str] = field(default_factory=list)


def use_agent_mode() -> bool:
    """Whether chat should run the tool-calling agent."""
    mode = (get_settings().agent_mode or "auto").strip().lower()
    if mode in {"rules", "rule", "off", "classify"}:
        return False
    if mode in {"agent", "tools", "tool"}:
        return llm_enabled()
    # auto
    return llm_enabled()


def _parse_args(raw: str | dict | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _tool_search(ctx: _ToolContext, args: dict[str, Any]) -> str:
    query = str(args.get("query") or ctx.message).strip() or ctx.message
    file_hits = session_file_hits(ctx.db, ctx.session.id)
    hits = retrieve(ctx.db, user=ctx.user, query=query, top_k=5)
    all_hits = file_hits + hits
    ctx.citations = [
        {
            "document_id": h["document_id"],
            "title": h["title"],
            "category": h["category"],
            "snippet": h["content"][:180],
            "score": h["score"],
        }
        for h in all_hits
    ]
    if not all_hits:
        return "检索结果为空：当前会话文件与公司知识库中没有足够相关内容。"
    from corp_os.models.document import Document
    from corp_os.services.library_files import download_url_for

    blocks = []
    for i, h in enumerate(all_hits, start=1):
        tag = "本会话文件" if h.get("category") == "session_file" else "知识库"
        extra = ""
        doc = ctx.db.get(Document, int(h["document_id"]))
        if doc and (doc.filename or "").lower().endswith((".pptx", ".docx", ".pdf", ".xlsx")):
            extra = f"\n原文件下载：{download_url_for(doc.id)}"
        blocks.append(
            f"[{i}]（{tag}）《{h['title']}》(score={h.get('score')})\n{h['content'].strip()}{extra}"
        )
    return "检索到以下资料，请据此回答用户：\n\n" + "\n\n".join(blocks)


def _tool_read_session_files(ctx: _ToolContext, _args: dict[str, Any]) -> str:
    hits = session_file_hits(ctx.db, ctx.session.id)
    if not hits:
        return "当前对话没有暂存文件。请先点 + 发送文件。"
    ctx.citations = [
        {
            "document_id": h["document_id"],
            "title": h["title"],
            "category": h["category"],
            "snippet": h["content"][:180],
            "score": h["score"],
        }
        for h in hits
    ]
    blocks = [f"《{h['title']}》\n{h['content']}" for h in hits]
    return (
        "以下是本对话暂存文件（尚未写入公司知识库）。"
        "可据此回答用户问题；若用户要入库，再调用 publish_to_knowledge_base。\n\n"
        + "\n\n".join(blocks)
    )


def _tool_share_library_file(ctx: _ToolContext, args: dict[str, Any]) -> str:
    from corp_os.services.library_files import share_document_download

    doc_id = args.get("document_id")
    query = args.get("query")
    if query is None or not str(query).strip():
        query = ctx.message
    return share_document_download(
        ctx.db,
        user=ctx.user,
        session_id=ctx.session.id,
        document_id=int(doc_id) if doc_id is not None else None,
        query=str(query) if query is not None else None,
    )


def _tool_publish_library(ctx: _ToolContext, args: dict[str, Any]) -> str:
    doc_id = args.get("document_id")
    return publish_staged_to_library(
        ctx.db,
        user=ctx.user,
        session_id=ctx.session.id,
        document_id=int(doc_id) if doc_id is not None else None,
    )


def _tool_generate_word(ctx: _ToolContext, args: dict[str, Any]) -> str:
    title = str(args.get("title") or "").strip() or "未命名文档"
    sections = args.get("sections")
    body = args.get("body")
    if sections is not None and not isinstance(sections, list):
        sections = None
    doc = generate_word(
        title=title,
        sections=sections if isinstance(sections, list) else None,
        body=str(body) if body is not None else None,
        username=ctx.user.username,
    )
    ctx.generated_urls.append(doc.download_url)
    return format_tool_result(doc, "Word 文档")


def _tool_generate_powerpoint(ctx: _ToolContext, args: dict[str, Any]) -> str:
    from pathlib import Path

    from corp_os.services.session_files import find_library_pptx, find_session_pptx

    title = str(args.get("title") or "").strip() or "未命名演示"
    slides = args.get("slides") or args.get("sections") or []
    if not isinstance(slides, list):
        slides = []

    def _as_bool(val: Any, default: bool | None = None) -> bool | None:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        s = str(val).strip().lower()
        if s in {"1", "true", "yes", "y", "是"}:
            return True
        if s in {"0", "false", "no", "n", "否"}:
            return False
        return default

    msg = ctx.message or ""
    tpl_id = args.get("template_document_id")
    use_company = _as_bool(args.get("use_company_template"), None)
    use_tpl = _as_bool(args.get("use_session_template"), None)
    wants_blank = any(k in msg for k in ("空白模板", "不要模板", "不用模板", "纯空白"))
    mentions_company_tpl = any(
        k in msg for k in ("公司模板", "公司模版", "知识库模板", "知识库模版", "商务汇报")
    )

    # Template is optional (page size only). Never block generation for missing template.
    if use_tpl is None:
        use_tpl = not wants_blank
    if use_company is None:
        use_company = mentions_company_tpl or (use_tpl and not wants_blank)

    template_doc = None
    template_path = None
    if not wants_blank and (tpl_id is not None or use_tpl or use_company):
        try:
            if use_company and tpl_id is None:
                template_doc = find_library_pptx(ctx.db, ctx.user, prefer_template=True)
                if template_doc is None:
                    template_doc = find_session_pptx(ctx.db, ctx.session.id)
            else:
                template_doc = resolve_pptx_template(
                    ctx.db,
                    ctx.user,
                    ctx.session.id,
                    document_id=int(tpl_id) if tpl_id is not None else None,
                )
        except Exception:  # noqa: BLE001
            logger.exception("resolve pptx template failed; continue without template")
            template_doc = None
        if template_doc is not None and Path(template_doc.stored_path).is_file():
            template_path = Path(template_doc.stored_path)
        else:
            template_doc = None

    try:
        doc = generate_powerpoint(
            title=title,
            slides=slides,
            username=ctx.user.username,
            template_path=template_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_powerpoint failed")
        return f"PPT 生成失败：{exc}。请稍后再试，或改用更少页的大纲。"
    if not doc.path.is_file():
        return "PPT 生成失败：文件未写入磁盘。"
    ctx.generated_urls.append(doc.download_url)
    tip = format_tool_result(doc, "PPT 演示文稿")
    if template_doc is not None:
        name = template_doc.title or template_doc.filename
        mode = getattr(doc, "template_mode", "") or ""
        if mode == "visual":
            tip += f"\n已套用模板《{name}》（#{template_doc.id}）的版式与背景（仅替换占位文字，未删页）。"
        elif mode == "size":
            tip += (
                f"\n已参考模板《{name}》（#{template_doc.id}）的页面尺寸；"
                f"该模板无可填占位符，已用稳定版式生成以保证可预览/下载。"
            )
        else:
            tip += f"\n已关联模板《{name}》（#{template_doc.id}）。"
    return tip


def _tool_generate_markdown(ctx: _ToolContext, args: dict[str, Any]) -> str:
    title = str(args.get("title") or "").strip() or "未命名文档"
    sections = args.get("sections")
    body = args.get("body")
    if sections is not None and not isinstance(sections, list):
        sections = None
    doc = generate_markdown(
        title=title,
        sections=sections if isinstance(sections, list) else None,
        body=str(body) if body is not None else None,
        username=ctx.user.username,
    )
    ctx.generated_urls.append(doc.download_url)
    return format_tool_result(doc, "Markdown 文档")


def _tool_employees(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("employees", user=ctx.user, db=ctx.db, keyword=args.get("keyword"))


def _tool_get_employee(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("employee_get", user=ctx.user, db=ctx.db, employee_id=args.get("employee_id"))


def _tool_create_employee(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("employee_create", user=ctx.user, db=ctx.db, **args)


def _tool_update_employee(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("employee_update", user=ctx.user, db=ctx.db, **args)


def _tool_delete_employee(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("employee_delete", user=ctx.user, db=ctx.db, employee_id=args.get("employee_id"))


def _tool_inventory(ctx: _ToolContext, _args: dict[str, Any]) -> str:
    return run_erp_tool("inventory", user=ctx.user, db=ctx.db)


def _tool_products(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("products", user=ctx.user, db=ctx.db, keyword=args.get("keyword"))


def _tool_warehouses(ctx: _ToolContext, _args: dict[str, Any]) -> str:
    return run_erp_tool("warehouses", user=ctx.user, db=ctx.db)


def _tool_create_product(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("product_create", user=ctx.user, db=ctx.db, **args)


def _tool_update_product(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("product_update", user=ctx.user, db=ctx.db, **args)


def _tool_delete_product(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("product_delete", user=ctx.user, db=ctx.db, product_id=args.get("product_id"))


def _tool_create_warehouse(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("warehouse_create", user=ctx.user, db=ctx.db, **args)


def _tool_stock_in(ctx: _ToolContext, args: dict[str, Any]) -> str:
    return run_erp_tool("stock_in", user=ctx.user, db=ctx.db, **args)


def _tool_erp_health(ctx: _ToolContext, _args: dict[str, Any]) -> str:
    return run_erp_tool("health", user=ctx.user, db=ctx.db)


def _tool_erp_find_operations(ctx: _ToolContext, args: dict[str, Any]) -> str:
    from corp_os.services.erp_catalog import find_operations, format_operations_answer

    query = str(args.get("query") or "").strip()
    tag = str(args.get("tag") or "").strip() or None
    limit = args.get("limit")
    try:
        lim = int(limit) if limit is not None else 12
    except (TypeError, ValueError):
        lim = 12
    try:
        ops = find_operations(query, tag=tag, limit=lim)
    except Exception as exc:  # noqa: BLE001
        logger.exception("erp_find_operations failed")
        return f"无法加载 ERP OpenAPI 目录：{exc}。请确认 CORP_OS_ERP_BASE_URL 可访问 /openapi.json。"
    return format_operations_answer(ops, query=query or (tag or ""))


def _tool_erp_call(ctx: _ToolContext, args: dict[str, Any]) -> str:
    method = str(args.get("method") or "GET")
    path = str(args.get("path") or "")
    params = args.get("params")
    body = args.get("body")
    if params is not None and not isinstance(params, dict):
        return "params 必须是 JSON 对象"
    if body is not None and not isinstance(body, dict):
        return "body 必须是 JSON 对象"
    return gateway_call(
        user=ctx.user,
        db=ctx.db,
        method=method,
        path=path,
        params=params,
        body=body,
    )


_HANDLERS: dict[str, Callable[[_ToolContext, dict[str, Any]], str]] = {
    "search_company_knowledge": _tool_search,
    "read_session_files": _tool_read_session_files,
    "share_library_file": _tool_share_library_file,
    "publish_to_knowledge_base": _tool_publish_library,
    "generate_word": _tool_generate_word,
    "generate_powerpoint": _tool_generate_powerpoint,
    "generate_markdown": _tool_generate_markdown,
    "list_employees": _tool_employees,
    "get_employee": _tool_get_employee,
    "create_employee": _tool_create_employee,
    "update_employee": _tool_update_employee,
    "delete_employee": _tool_delete_employee,
    "list_inventory": _tool_inventory,
    "list_products": _tool_products,
    "list_warehouses": _tool_warehouses,
    "create_product": _tool_create_product,
    "update_product": _tool_update_product,
    "delete_product": _tool_delete_product,
    "create_warehouse": _tool_create_warehouse,
    "stock_in": _tool_stock_in,
    "check_erp_health": _tool_erp_health,
    "erp_find_operations": _tool_erp_find_operations,
    "erp_call": _tool_erp_call,
}


def execute_tool(name: str, args: dict[str, Any], ctx: _ToolContext) -> str:
    handler = _HANDLERS.get(name)
    if not handler:
        return f"未知工具：{name}"
    if not can_use_tool(ctx.db, ctx.user, name):
        return tool_denied_message(name)
    try:
        return handler(ctx, args)
    except Exception as exc:  # noqa: BLE001
        logger.exception("tool %s failed", name)
        return f"工具 {name} 执行失败：{exc}"


def _assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize provider message for multi-turn tool history."""
    out: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content"),
    }
    tool_calls = message.get("tool_calls")
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out



_GENERATED_URL_RE = re.compile(r"/api/v1/chat/generated/[a-f0-9]{8,32}", re.IGNORECASE)
_EMPTY_DOWNLOAD_LINE_RE = re.compile(
    r"(?:\*\*)?下载地址(?:\*\*)?[：:]\s*(?:`+\s*`+|`\s*`)?\s*",
    re.IGNORECASE,
)
_CLAIMS_FILE_READY_RE = re.compile(
    r"(已生成|已就绪|下载地址|点[「\"']?\s*预览|点[「\"']?\s*下载)",
    re.IGNORECASE,
)


def _message_wants_docgen(text: str) -> bool:
    """Local copy of graph.is_docgen_intent to avoid circular imports."""
    t = (text or "").strip()
    if not t:
        return False
    wants_gen = any(
        k in t for k in ("生成", "制作", "做一份", "做个", "写一份", "出一份", "弄一份", "导出")
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
        k in t for k in ("PPT", "ppt", "Word", "word", "幻灯")
    )
    return (wants_gen and kind) or from_template


def _sanitize_generated_links(answer: str, real_urls: list[str]) -> str:
    """Replace hallucinated download links; ensure real tool URLs are present."""
    text = answer or ""
    real = [u for u in real_urls if u]
    real_set = set(real)
    found = _GENERATED_URL_RE.findall(text)
    for url in found:
        if url not in real_set:
            replacement = real[-1] if real else ""
            text = text.replace(url, replacement)
    # Clean empty "下载地址：** ``" left after stripping hallucinations.
    text = _EMPTY_DOWNLOAD_LINE_RE.sub("", text)
    if real and not any(u in text for u in real):
        text = text.rstrip() + "\n\n下载地址：\n" + "\n".join(real)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _fallback_docgen_tool(ctx: "_ToolContext") -> tuple[str, str]:
    """When the LLM skips tools (common with thinking models), generate locally."""
    msg = (ctx.message or "").strip()
    low = msg.lower()
    title = re.sub(
        r"(请|帮我|麻烦|再|重新)?(生成|制作|做一份|做个|写一份|出一份|弄一份|导出)\s*",
        "",
        msg,
        count=1,
    ).strip() or "未命名文档"
    title = re.sub(
        r"(PPT|ppt|pptx|幻灯片|演示文稿|Word|word|docx|Markdown|markdown|\.md)+",
        "",
        title,
    ).strip()
    title = title[:80] or "未命名文档"

    if any(k in msg for k in ("PPT", "ppt", "pptx", "幻灯片", "演示文稿")):
        name = "generate_powerpoint"
        args: dict[str, Any] = {
            "title": title if title != "未命名文档" else "演示文稿",
            "slides": [
                {"title": "概述", "bullets": [msg[:200] or "内容概要"]},
                {"title": "要点一", "bullets": ["请补充具体内容"]},
                {"title": "要点二", "bullets": ["请补充具体内容"]},
            ],
            "use_company_template": True,
        }
    elif any(k in low for k in ("markdown", ".md")) or "md文档" in msg:
        name = "generate_markdown"
        args = {
            "title": title,
            "sections": [{"heading": "概述", "paragraphs": [msg[:500] or title]}],
        }
    elif any(k in low for k in ("word", "docx")):
        name = "generate_word"
        args = {
            "title": title,
            "sections": [{"heading": "正文", "paragraphs": [msg[:800] or title]}],
        }
    else:
        name = "generate_powerpoint"
        args = {
            "title": title if title != "未命名文档" else "演示文稿",
            "slides": [
                {"title": "概述", "bullets": [msg[:200] or "内容概要"]},
                {"title": "要点", "bullets": ["请按需补充内容"]},
            ],
            "use_company_template": True,
        }
    return name, execute_tool(name, args, ctx)


def _finalize_agent_answer(answer: str, ctx: "_ToolContext") -> str:
    """Ensure download buttons can render: real URLs stay; fake claims without files are corrected."""
    text = _sanitize_generated_links(answer, ctx.generated_urls)
    if ctx.generated_urls:
        return text

    # No file was produced this turn — don't leave "点预览/下载" with zero buttons.
    if _message_wants_docgen(ctx.message) and _CLAIMS_FILE_READY_RE.search(text):
        return (
            "刚才没有真正生成文件（未成功调用生成工具），所以没有可预览/下载的链接。\n"
            "请再说一次「生成 PPT」或「生成 Word」，稍等工具跑完即可。"
        )
    return text


def run_tool_agent(
    db: Session,
    *,
    user: User,
    session: ChatSession,
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> AgentResult:
    """ReAct-style tool loop via OpenAI-compatible function calling."""
    if not llm_enabled():
        return AgentResult(
            answer="Agent 需要启用 LLM（CORP_OS_LLM_PROVIDER=openai_compatible 并配置密钥）。",
            action="chat.agent_unavailable",
        )

    ctx = _ToolContext(db=db, user=user, session=session, message=message)
    # 1) build model  2) bind tools  3) ReAct loop
    # Always tool_choice=auto: thinking/reasoning models reject tool_choice=required.
    bound_model = get_chat_model().bind_tools(TOOL_SPECS, tool_choice="auto")
    messages: list[dict[str, Any]] = [{"role": "system", "content": build_agent_system_prompt(user)}]
    for msg in history or []:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    max_steps = max(1, int(get_settings().agent_max_steps))
    trace: list[str] = []
    wants_docgen = _message_wants_docgen(message)
    docgen_tools = {"generate_powerpoint", "generate_word", "generate_markdown"}
    nudged_docgen = False

    for step in range(max_steps):
        try:
            ai_message = bound_model.invoke(dict_messages_to_lc(messages))
            assistant = lc_message_to_dict(ai_message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent LLM turn failed")
            # Last resort for docgen: still produce a downloadable file without the LLM.
            if wants_docgen and not ctx.generated_urls:
                name, tip = _fallback_docgen_tool(ctx)
                trace.append(name)
                return AgentResult(
                    answer=_finalize_agent_answer(tip, ctx),
                    citations=ctx.citations,
                    action="chat.agent_docgen_fallback",
                    tool_trace=trace,
                )
            answer = _finalize_agent_answer(f"Agent 调用大模型失败：{exc}", ctx)
            return AgentResult(
                answer=answer,
                citations=ctx.citations,
                action="chat.agent_error",
                tool_trace=trace,
            )

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            answer = (assistant.get("content") or "").strip()
            # Soft nudge once (still tool_choice=auto — compatible with thinking models).
            if (
                wants_docgen
                and not (set(trace) & docgen_tools)
                and not ctx.generated_urls
                and not nudged_docgen
                and step < max_steps - 1
            ):
                nudged_docgen = True
                if answer:
                    messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "必须调用 generate_powerpoint / generate_word / generate_markdown "
                            "之一真正生成文件，不要只文字描述。生成后把工具返回的下载地址原样给出。"
                        ),
                    }
                )
                continue

            # Hard fallback: local tool so preview/download buttons always appear.
            if wants_docgen and not ctx.generated_urls:
                name, tip = _fallback_docgen_tool(ctx)
                trace.append(name)
                answer = tip if not answer else f"{answer.rstrip()}\n\n{tip}"
            elif not answer:
                answer = "我暂时没有生成有效回答，请换个问法再试。"
            answer = _finalize_agent_answer(answer, ctx)
            return AgentResult(
                answer=answer,
                citations=ctx.citations,
                action="chat.agent",
                tool_trace=trace,
            )

        messages.append(_assistant_message_for_history(assistant))
        for call in tool_calls:
            fn = call.get("function") or {}
            name = str(fn.get("name") or "")
            args = _parse_args(fn.get("arguments"))
            trace.append(name)
            logger.info("agent step=%s tool=%s args=%s", step, name, args)
            result_text = execute_tool(name, args, ctx)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "content": result_text,
                }
            )

    # Steps exhausted — still surface any files already produced this turn.
    fallback = "工具调用轮次已用尽，请把问题说得更具体一些再试。"
    if wants_docgen and not ctx.generated_urls:
        name, tip = _fallback_docgen_tool(ctx)
        trace.append(name)
        fallback = tip
    elif ctx.generated_urls:
        fallback = (
            "生成过程轮次已用尽，但文件已产出。\n"
            + "\n".join(f"下载地址：{u}" for u in ctx.generated_urls)
            + "\n请点「预览」或「下载」。"
        )
    return AgentResult(
        answer=_finalize_agent_answer(fallback, ctx),
        citations=ctx.citations,
        action="chat.agent_max_steps",
        tool_trace=trace,
    )
