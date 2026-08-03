from corp_os.services.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("demo123")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("demo123", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("demo123", None)


def test_access_token_roundtrip():
    token = create_access_token(username="alice", expires_minutes=60)
    payload = decode_access_token(token)
    assert payload["sub"] == "alice"
