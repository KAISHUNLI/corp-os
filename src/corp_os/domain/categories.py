"""Company document taxonomy for uploads and RAG labeling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    code: str
    name: str
    description: str


CATEGORIES: list[Category] = [
    Category("policy", "规章制度", "制度、规范、员工手册等"),
    Category("notice", "内部通知", "公告、会议纪要、内部通报"),
    Category("requirement", "客户需求", "客户需求文档、变更说明、沟通纪要"),
    Category("tech", "技术资料", "接口文档、实施方案、运维手册"),
    Category("contract", "合同协议", "合同及补充协议"),
    Category("invoice", "发票票据", "发票、对账单等"),
    Category("hr", "人事行政", "组织、考勤、行政类材料"),
    Category("other", "其他资料", "未归入以上分类的内部文件"),
]

CATEGORY_MAP = {c.code: c for c in CATEGORIES}

# 公司知识入库：类目 → 可上传的部门（对应主管）。老板/管理员不受限。
# 空集合表示仅允许「本部门可见」材料由本部门主管上传，不可直接发全公司。
CATEGORY_OWNER_DEPARTMENTS: dict[str, frozenset[str]] = {
    "policy": frozenset({"hr", "exec"}),
    "notice": frozenset({"hr", "exec"}),
    "hr": frozenset({"hr"}),
    "tech": frozenset({"delivery"}),
    "requirement": frozenset({"delivery"}),
    "contract": frozenset({"legal"}),
    "invoice": frozenset({"finance"}),
    "other": frozenset(),
}


def owner_departments_for(category: str) -> frozenset[str]:
    return CATEGORY_OWNER_DEPARTMENTS.get(category, frozenset())


def category_owner_hint(category: str) -> str:
    owners = owner_departments_for(category)
    if not owners:
        return "本部门主管（仅部门可见）或老板"
    names = {
        "hr": "人事行政",
        "exec": "管理层",
        "delivery": "交付/研发",
        "legal": "法务",
        "finance": "财务",
    }
    label = "、".join(names.get(c, c) for c in sorted(owners))
    return f"{label}主管或老板"
