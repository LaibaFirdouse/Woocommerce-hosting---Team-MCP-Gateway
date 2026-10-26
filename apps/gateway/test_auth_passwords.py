from auth.passwords import hash_password, verify_password


def test_hash_and_verify_password() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_password_rejects_empty_inputs() -> None:
    hashed = hash_password("validpassword")
    assert not verify_password("", hashed)
    assert not verify_password("validpassword", "")
