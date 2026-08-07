from corp_os.rag.graph import classify_route, is_docgen_intent


def test_classify_kind_correct():
    route, _, _ = classify_route("这是车票")
    assert route == "kind_correct"


def test_classify_governance_decide():
    route, req_id, action = classify_route("批准 #12")
    assert route == "governance_decide"
    assert req_id == 12
    assert action == "approve"


def test_classify_governance_pending():
    route, _, _ = classify_route("待我审批")
    assert route == "governance_pending"


def test_classify_expense():
    route, _, _ = classify_route("这些够不够报销？还缺什么")
    assert route == "expense"


def test_classify_erp_inventory():
    route, _, _ = classify_route("查一下库存还有多少")
    assert route == "erp_inventory"


def test_classify_erp_employees():
    route, _, _ = classify_route("员工名单给我看看")
    assert route == "erp_employees"


def test_classify_erp_employees_how_many():
    for q in ("多少员工", "有多少员工", "公司有几个员工", "在职人数", "员工人数多少"):
        route, _, _ = classify_route(q)
        assert route == "erp_employees", q


def test_classify_erp_employee_write():
    assert classify_route("入职 王五")[0] == "erp_employee_create"
    assert classify_route("删除员工 #3")[0] == "erp_employee_delete"
    assert classify_route("新建产品 螺丝")[0] == "erp_product_create"


def test_classify_smalltalk():
    route, _, _ = classify_route("你好")
    assert route == "smalltalk"


def test_classify_help_intent():
    assert classify_route("你都能干啥")[0] == "help"
    assert classify_route("你能做什么")[0] == "help"
    assert classify_route("有什么功能")[0] == "help"


def test_classify_identity_intent():
    assert classify_route("我是谁")[0] == "identity"
    assert classify_route("我的角色是什么")[0] == "identity"
    assert classify_route("我的账号")[0] == "identity"


def test_classify_library_publish():
    for q in ("上传到知识库", "写入知识库", "正式上传", "发布到知识库"):
        assert classify_route(q)[0] == "library_publish", q


def test_classify_erp_stock_in_not_confused_with_kb():
    assert classify_route("仓库入库需要什么")[0] == "erp_stock_in_help"
    assert classify_route("确认入库")[0] == "erp_stock_in_help"
    # bare 入库 no longer forces ERP
    assert classify_route("入库")[0] == "rag"


def test_classify_rag_default():
    route, _, _ = classify_route("迟到五次有什么处分")
    assert route == "rag"


def test_is_docgen_intent():
    assert is_docgen_intent("根据这个模板生成 PPT")
    assert is_docgen_intent("帮我做个项目汇报幻灯片")
    assert is_docgen_intent("生成一份周报 Word")
    assert is_docgen_intent("按模版制作演示文稿")
    assert not is_docgen_intent("迟到五次有什么处分")
    assert not is_docgen_intent("上传到知识库")
