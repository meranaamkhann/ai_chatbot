import sys

import pytest


@pytest.fixture
def crypto(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "tkGtljKdQPNaeYmt3KQr--k-_13LdA-qc0tUjoeDpLY=")
    sys.modules.pop("crypto", None)
    import crypto as crypto_module

    yield crypto_module
    sys.modules.pop("crypto", None)


def test_round_trip(crypto):
    original = "I've had chest tightness for two days, should I be worried?"
    ciphertext = crypto.encrypt_text(original)
    assert crypto.decrypt_text(ciphertext) == original


def test_ciphertext_does_not_contain_plaintext(crypto):
    original = "sensitive health details about my condition"
    ciphertext = crypto.encrypt_text(original)
    assert "sensitive" not in ciphertext
    assert "health" not in ciphertext
    assert original not in ciphertext


def test_same_plaintext_encrypts_differently_each_time(crypto):
    """Fernet includes a random IV, so encrypting the same message twice
    should not produce identical ciphertext — otherwise an attacker with DB
    access could spot repeated messages just from matching ciphertext."""
    a = crypto.encrypt_text("hello")
    b = crypto.encrypt_text("hello")
    assert a != b
    assert crypto.decrypt_text(a) == crypto.decrypt_text(b) == "hello"


def test_missing_encryption_key_raises_on_import(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    # crypto.py defensively calls load_dotenv() on import (see its module
    # docstring) so it doesn't depend on being imported in a particular
    # order. That's a good thing in production, but it means a real local
    # .env file with ENCRYPTION_KEY set would silently repopulate the
    # variable we just deleted, defeating this test's whole point. Patch
    # load_dotenv to a no-op so this test genuinely exercises "the key is
    # absent," regardless of what .env file happens to exist on disk.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    sys.modules.pop("crypto", None)
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        import crypto  # noqa: F401
    sys.modules.pop("crypto", None)


def test_decrypt_with_wrong_key_fails_visibly(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "tkGtljKdQPNaeYmt3KQr--k-_13LdA-qc0tUjoeDpLY=")
    sys.modules.pop("crypto", None)
    import crypto as crypto_a

    ciphertext = crypto_a.encrypt_text("secret")

    monkeypatch.setenv("ENCRYPTION_KEY", "Zx9pQwErTyUiOpAsDfGhJkLzXcVbNm12QwErTyUiOpA=")
    sys.modules.pop("crypto", None)
    import crypto as crypto_b

    result = crypto_b.decrypt_text(ciphertext)
    assert "can't be decrypted" in result
    sys.modules.pop("crypto", None)
