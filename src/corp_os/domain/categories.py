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
