from __future__ import annotations

from sqlalchemy.orm import Session

from corp_os.config import get_settings
from corp_os.models.document import Document
from corp_os.models.iam import Department, Role, User
from corp_os.rag.store import index_document
from corp_os.services.security import hash_password


def seed_if_empty(db: Session) -> None:
    if db.query(User).first():
        return

    settings = get_settings()
    password_hash = hash_password(settings.demo_password)

    db.add_all(
        [
            Department(code="legal", name="法务部"),
            Department(code="finance", name="财务部"),
            Department(code="delivery", name="交付实施部"),
            Department(code="hr", name="人力资源部"),
            Department(code="exec", name="管理层"),
            Role(code="employee", name="员工", permissions="chat,upload"),
            Role(code="legal", name="法务", permissions="chat,upload"),
            Role(code="finance", name="财务", permissions="chat,upload,finance.read"),
            Role(code="boss", name="老板", permissions="*"),
            Role(code="admin", name="系统管理员", permissions="*"),
            User(
                username="alice",
                display_name="张三",
                password_hash=password_hash,
                department_code="delivery",
                role_code="employee",
                dingtalk_userid="ding_alice",
                is_dept_manager=False,
            ),
            User(
                username="delivery_manager",
                display_name="交付主管",
                password_hash=password_hash,
                department_code="delivery",
                role_code="employee",
                dingtalk_userid="ding_delivery_mgr",
                is_dept_manager=True,
            ),
            User(
                username="legal01",
                display_name="李法务",
                password_hash=password_hash,
                department_code="legal",
                role_code="legal",
                dingtalk_userid="ding_legal01",
                is_dept_manager=True,
            ),
            User(
                username="finance01",
                display_name="王财务",
                password_hash=password_hash,
                department_code="finance",
                role_code="finance",
                dingtalk_userid="ding_finance01",
                is_dept_manager=True,
            ),
            User(
                username="boss",
                display_name="老板",
                password_hash=password_hash,
                department_code="exec",
                role_code="boss",
                dingtalk_userid="ding_boss",
                is_dept_manager=False,
            ),
            User(
                username="admin",
                display_name="管理员",
                password_hash=password_hash,
                department_code="hr",
                role_code="admin",
                dingtalk_userid="ding_admin",
                is_dept_manager=False,
            ),
        ]
    )
    db.flush()

    samples = [
        Document(
            title="公司章程（摘要）",
            filename="articles.txt",
            stored_path="seed://articles.txt",
            category="policy",
            visibility="company",
            status="active",
            uploaded_by="admin",
            department_code="hr",
            full_text=(
                "公司章程（摘要）\n"
                "第一条 公司宗旨是为客户提供工业软件与数字化服务。\n"
                "第二条 股东会是公司最高权力机构。\n"
                "第三条 董事会负责重大经营决策，总经理负责日常经营。\n"
                "第四条 公司实行劳动合同制，员工应遵守公司规章制度。"
            ),
            text_excerpt="公司实行劳动合同制，员工应遵守公司规章制度。",
        ),
        Document(
            title="员工考勤与纪律管理办法",
            filename="attendance.txt",
            stored_path="seed://attendance.txt",
            category="policy",
            visibility="company",
            status="active",
            uploaded_by="admin",
            department_code="hr",
            full_text=(
                "员工考勤与纪律管理办法\n"
                "一、迟到：上班时间后到达视为迟到。\n"
                "二、当月迟到 1-2 次：口头提醒。\n"
                "三、当月迟到 3-4 次：书面警告，并扣除当月全勤奖。\n"
                "四、当月迟到 5 次及以上：视为严重违反劳动纪律，可给予记过处分，"
                "并取消季度奖金；情节严重的，公司可依法解除劳动合同。\n"
                "五、因公共交通故障等不可抗力导致迟到，员工应及时报备并提供证明，经部门负责人确认后可不计迟到。"
            ),
            text_excerpt="当月迟到 5 次及以上可记过并取消季度奖金。",
        ),
        Document(
            title="差旅报销制度",
            filename="travel.txt",
            stored_path="seed://travel.txt",
            category="policy",
            visibility="company",
            status="active",
            uploaded_by="admin",
            department_code="finance",
            full_text=(
                "差旅报销制度\n"
                "一、提交时限：出差结束后 7 个工作日内提交报销。\n"
                "二、必备材料：\n"
                "1）出差审批单（出发前已审批通过）\n"
                "2）交通票据：火车票/机票/汽车票原件或电子客票行程单\n"
                "3）发票：住宿发票、市内交通发票等合法有效发票\n"
                "三、建议材料：行程说明或出差报告（便于财务核对事由）\n"
                "四、住宿标准：经理级不超过 600 元/晚，员工不超过 400 元/晚。\n"
                "五、缺审批单、缺交通票据或缺发票的，财务应退回补齐后再审。\n"
                "六、市内交通实报实销，需保留发票。"
            ),
            text_excerpt="报销必备：出差审批单、交通票据、发票；缺少则退回补齐。",
        ),
        Document(
            title="法务合同审查清单（内部）",
            filename="legal-checklist.txt",
            stored_path="seed://legal-checklist.txt",
            category="policy",
            visibility="role",
            visibility_target="legal",
            status="active",
            uploaded_by="legal01",
            department_code="legal",
            full_text=(
                "法务合同审查清单\n"
                "必须核对：付款、验收、质保、违约责任。\n"
                "仅法务角色可见。"
            ),
            text_excerpt="必须核对付款验收质保违约责任。",
        ),
        Document(
            title="2026年3月员工薪资表（机密）",
            filename="salary-2026-03.txt",
            stored_path="seed://salary-2026-03.txt",
            category="hr",
            visibility="role",
            visibility_target="finance",
            status="active",
            uploaded_by="finance01",
            department_code="finance",
            full_text=(
                "2026年3月员工薪资表（机密）\n"
                "仅财务与老板可见。\n"
                "张三（交付）基本工资 12000，绩效 2000，实发 14000。\n"
                "李法务基本工资 15000，绩效 3000，实发 18000。\n"
                "王财务基本工资 14000，绩效 2500，实发 16500。\n"
                "合计应发 48500，个税代扣合计 3200。"
            ),
            text_excerpt="仅财务可见的薪资汇总表。",
        ),
        Document(
            title="2026年一季度财务报表（机密）",
            filename="finance-report-q1.txt",
            stored_path="seed://finance-report-q1.txt",
            category="other",
            visibility="department",
            visibility_target="finance",
            status="active",
            uploaded_by="finance01",
            department_code="finance",
            full_text=(
                "2026年一季度财务报表（机密）\n"
                "仅财务部门与老板可见。\n"
                "营业收入 1280 万，营业成本 760 万，毛利 520 万。\n"
                "期间费用 210 万，净利润 246 万。\n"
                "经营性现金流净额 180 万，应收账款周转天数 52 天。"
            ),
            text_excerpt="一季度净利润 246 万，仅财务可见。",
        ),
    ]
    db.add_all(samples)
    db.flush()
    for doc in samples:
        index_document(db, doc)
    db.commit()
