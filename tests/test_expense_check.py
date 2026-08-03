from corp_os.services.expense_check import normalize_kind, TRAVEL_CHECKLIST


def test_normalize_kind_from_filename():
    assert normalize_kind(None, "高铁车票.png", "", "") == "train_ticket"
    assert normalize_kind(None, "住宿发票.jpg", "", "") == "invoice"
    assert normalize_kind("审批单", "scan.pdf", "", "") == "travel_approval"


def test_checklist_has_required_items():
    codes = {i.code for i in TRAVEL_CHECKLIST if i.required}
    assert {"travel_approval", "train_ticket", "invoice"} <= codes
