"""Travel/expense reimbursement pre-check against company policy + uploaded materials."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from corp_os.models.rag import ChatAttachment
from corp_os.rag.store import retrieve
from corp_os.models.iam import User


@dataclass(frozen=True)
class RequiredItem:
    code: str
    name: str
    required: bool = True
    hints: tuple[str, ...] = ()


# Baseline checklist derived from company travel policy (also reinforced via RAG hits).
TRAVEL_CHECKLIST: list[RequiredItem] = [
    RequiredItem("travel_approval", "出差审批单", True, ("审批", "出差申请", "approval")),
    RequiredItem("train_ticket", "交通票据（火车票/机票/汽车票）", True, ("车票", "高铁", "机票", "火车", "行程单")),
    RequiredItem("invoice", "发票（住宿/交通等）", True, ("发票", "增值税", "invoice")),
    RequiredItem("itinerary", "行程说明/出差报告", False, ("行程", "报告", "说明")),
]


KIND_ALIASES = {
    "invoice": "invoice",
    "发票": "invoice",
    "hotel_invoice": "invoice",
    "住宿发票": "invoice",
    "train_ticket": "train_ticket",
    "车票": "train_ticket",
    "火车票": "train_ticket",
    "机票": "train_ticket",
    "travel_approval": "travel_approval",
    "审批单": "travel_approval",
    "出差审批": "travel_approval",
    "itinerary": "itinerary",
    "行程": "itinerary",
    "other": "other",
}


def normalize_kind(raw: str | None, filename: str = "", note: str = "", text: str = "") -> str:
    blob = f"{raw or ''} {filename} {note} {text}".lower()
    # Prefer explicit raw mapping
    if raw:
        key = raw.strip().lower()
        if key in KIND_ALIASES:
            return KIND_ALIASES[key]
    for token, kind in (
        ("审批", "travel_approval"),
        ("出差申请", "travel_approval"),
        ("火车票", "train_ticket"),
        ("高铁", "train_ticket"),
        ("机票", "train_ticket"),
        ("车票", "train_ticket"),
        ("行程单", "train_ticket"),
        ("发票", "invoice"),
        ("invoice", "invoice"),
        ("行程说明", "itinerary"),
        ("出差报告", "itinerary"),
    ):
        if token in blob:
            return kind
    return "other"


def list_session_attachments(db: Session, session_id: int) -> list[ChatAttachment]:
    return list(
        db.scalars(
            select(ChatAttachment).where(ChatAttachment.session_id == session_id).order_by(ChatAttachment.id.asc())
        )
    )


def is_expense_intent(message: str) -> bool:
    keys = ("报销", "发票", "车票", "差旅", "能报吗", "能不能报", "缺什么", "材料齐全")
    return any(k in message for k in keys)


def check_expense(
    db: Session,
    *,
    user: User,
    session_id: int,
    message: str,
) -> dict:
    attachments = list_session_attachments(db, session_id)
    present = {a.kind for a in attachments if a.kind != "other"}
    # Also infer from loose "other" labels
    for a in attachments:
        if a.kind == "other":
            present.add(normalize_kind(None, a.label or "", a.label or "", ""))

    missing_required: list[str] = []
    missing_optional: list[str] = []
    present_names: list[str] = []
    for item in TRAVEL_CHECKLIST:
        if item.code in present:
            present_names.append(item.name)
        elif item.required:
            missing_required.append(item.name)
        else:
            missing_optional.append(item.name)

    policy_hits = retrieve(db, user=user, query="差旅报销 所需材料 发票 车票 审批", top_k=3)
    can_pass = len(missing_required) == 0

    lines: list[str] = []
    lines.append("【报销预审结果】")
    if can_pass:
        lines.append("结论：按当前已上传材料，**基本满足差旅报销必备要件**，大概率可进入财务审核。")
        if missing_optional:
            lines.append("建议补齐（非强制但有助于更快过审）：" + "、".join(missing_optional))
    else:
        lines.append("结论：按当前材料，**暂不建议直接提交**，大概率会被退回。")
        lines.append("缺少的必备材料：")
        for name in missing_required:
            lines.append(f"- {name}")

    lines.append("")
    lines.append("本次会话已识别材料：")
    if attachments:
        for a in attachments:
            lines.append(f"- [{a.kind}] {a.label}")
    else:
        lines.append("- （还没有上传发票/车票等材料，请先点左下角 + 上传）")

    if policy_hits:
        lines.append("")
        lines.append("制度依据（RAG）：")
        for hit in policy_hits[:2]:
            lines.append(f"《{hit['title']}》：{hit['content'][:160].replace(chr(10), ' ')}")

    lines.append("")
    lines.append("说明：当前版本按文件名/备注识别材料类型；图片 OCR 可后续接入。预审不等于财务最终审批。")

    citations = [
        {
            "document_id": h["document_id"],
            "title": h["title"],
            "category": h["category"],
            "snippet": h["content"][:180],
            "score": h["score"],
        }
        for h in policy_hits
    ]
    return {
        "answer": "\n".join(lines),
        "citations": citations,
        "can_pass": can_pass,
        "missing_required": missing_required,
        "present": sorted(present),
        "question": message,
    }
