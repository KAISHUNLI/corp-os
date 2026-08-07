from pathlib import Path

from corp_os.services.library_files import download_url_for, resolve_library_file, share_document_download


def test_download_url_for():
    assert download_url_for(14) == "/api/v1/chat/library/14"


def test_resolve_and_share_company_pptx(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    pptx = tmp_path / "商务汇报.pptx"
    pptx.write_bytes(b"PK\x03\x04fake-pptx-content-for-test")

    doc = SimpleNamespace(
        id=14,
        title="商务汇报.pptx",
        filename="商务汇报.pptx",
        stored_path=str(pptx),
        status="active",
        visibility="company",
        visibility_target=None,
        uploaded_by="boss",
        department_code=None,
        content_type=None,
    )
    user = SimpleNamespace(username="alice", role_code="employee", department_code="hr")
    db = MagicMock()
    db.get.return_value = doc

    path, filename, media = resolve_library_file(db, user=user, document_id=14)
    assert path == pptx
    assert filename.endswith(".pptx")
    assert "presentation" in media

    tip = share_document_download(db, user=user, session_id=1, document_id=14)
    assert "/api/v1/chat/library/14" in tip
    assert "商务汇报" in tip
