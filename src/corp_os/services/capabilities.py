"""Capability / help copy for meta questions (not RAG)."""

from __future__ import annotations

import re

from corp_os.models.iam import User
from corp_os.services.governance import can_upload_company_knowledge
from corp_os.services.permissions import has_permission, is_elevated

_IDENTITY_RE = re.compile(
    r"(我是谁|我的身份|我的账号|我的角色|当前(登录|账号|用户|身份)|who\s*am\s*i|我叫什么)",
    re.IGNORECASE,
)


def is_identity_intent(text: str) -> bool:
    """User asking who *they* are (login account), not who the assistant is."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_IDENTITY_RE.search(t))


def build_identity_answer(user: User, history: list[dict] | None = None) -> str:
    from corp_os.rag.memory import recent_user_claims

    name = user.display_name or user.username
    dept = user.department_code or "未分配部门"
    role = user.role_code or "employee"
    lines = [
        f"你当前登录账号是 {user.username}（{name}）。",
        f"- 角色：{role}",
        f"- 部门：{dept}",
    ]
    if user.erp_username:
        lines.append(f"- 已绑定 ERP 账号：{user.erp_username}")
    else:
        lines.append("- ERP 账号：未绑定（查询 ERP 时可能走服务账号或受限）")

    claims = recent_user_claims(history)
    if claims:
        lines.append("")
        lines.append("本对话里你还说过：")
        for claim in claims:
            lines.append(f"- {claim}")
        lines.append("（聊天自称不会改变登录权限。）")
    else:
        lines.extend(
            [
                "",
                "身份以登录账号为准；聊天里自称「我是老板」不会改变权限。",
                "若要用老板权限，请用对应账号重新登录。",
            ]
        )
    return "\n".join(lines)


def is_help_intent(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if is_identity_intent(t):
        return False
    keys = (
        "你都能干啥",
        "你能干什么",
        "你能做什么",
        "你会什么",
        "你会干啥",
        "有什么功能",
        "功能介绍",
        "能力介绍",
        "怎么用",
        "如何使用",
        "帮我什么",
        "可以帮我什么",
        "你是谁",
        "介绍一下你",
        "what can you do",
        "help",
    )
    if any(k in t for k in keys):
        return True
    # short meta questions
    if t in {"功能", "能力", "菜单", "指令", "帮助", "?"}:
        return True
    return False


def build_help_answer(user: User, db=None) -> str:
    name = user.display_name or user.username
    can_author = can_upload_company_knowledge(user)
    lines = [
        f"{name}，我是 corp-os 公司智能助手。按你的账号权限，我可以帮你：",
        "",
        "1. 查制度/知识库（例：迟到五次有什么处分）",
        "2. 生成 Word / PPT / Markdown，并可套用公司 PPT 模板",
        "3. 会话文件提问、入库（有权限时）",
    ]
    if has_permission(db, user, "erp.employees") or is_elevated(user):
        lines.append("4. ERP 员工：查名单/人数；有写权限时可「入职 张三」「删除员工 #3」")
    if has_permission(db, user, "erp.inventory") or is_elevated(user):
        lines.append("5. ERP 库存/产品：查库存；有写权限时可「新建产品 螺丝」、入库等")
    if has_permission(db, user, "erp.health") or is_elevated(user):
        lines.append(
            "6. ERP 全站：可说「查销售订单 / 请假 / 毛利率」等，"
            "Agent 会通过 OpenAPI 检索并调用 company-er（localhost:8080 同源 API）"
        )
    if not any(x.startswith("4.") or x.startswith("5.") for x in lines):
        lines.append("4. ERP：当前角色未开通 erp.* 权限（可找管理员开通）")

    lines.extend(
        [
            "",
            "直接说具体业务问题即可，例如：「迟到五次有什么处分」「多少员工」。",
            "提示：点 + 发送文件后先暂存，可直接提问。",
            "也可说「生成一份周报 Word / Markdown」「做个汇报 PPT」，生成后可在对话里预览/下载。",
        ]
    )
    if can_author:
        lines.append("你有公司知识入库权限：可说「上传到知识库」；资料缺失时也可请我起草制度补充 Word。")
    else:
        lines.append("公司制度入库/修订仅对应主管或老板可操作；查不到条款时请联系主管或人事，勿自行当作制度修订。")
    lines.append("支持 pdf / docx / pptx / xlsx / csv / txt / md，以及 png/jpg/webp（OCR 需 tesseract）。")
    return "\n".join(lines)


def policy_authoring_prompt_rules(user: User | None) -> str:
    """Extra system-prompt rules: who may propose drafting/uploading company policy."""
    if user is not None and can_upload_company_knowledge(user):
        return (
            "【权限】当前用户是主管/老板/管理员，可提议：起草制度补充 Word、"
            "或在用户确认后引导「上传到知识库」。不要擅自声称已入库。"
        )
    return (
        "【权限】当前用户是普通员工，无权修订或上传公司制度。"
        "知识库没有相关明文条款时：只说明暂无依据，可建议换问法或联系主管/人事；"
        "禁止提议「帮你补充制度」「参照迟到标准起草早退规定」「生成制度修订 Word 并入库」等。"
        "仍可生成个人用途文档（个人周报/笔记），但不得表述为公司正式制度。"
    )
