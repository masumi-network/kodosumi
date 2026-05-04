"""
Tests for JWT encode/decode roundtrip.
Validates that the migration from python-jose to PyJWT works correctly.
"""
import pytest
import uuid

from kodosumi.service.jwt import encode_jwt_token, decode_jwt_token
from kodosumi.dtypes import Token


def test_jwt_roundtrip():
    """Encode a JWT and decode it back — payload must match."""
    role_id = str(uuid.uuid4())
    token_str = encode_jwt_token(role_id)
    assert isinstance(token_str, str)
    assert len(token_str) > 20

    decoded = decode_jwt_token(token_str)
    assert isinstance(decoded, Token)
    assert decoded.sub == role_id
    assert decoded.exp > decoded.iat


def test_jwt_invalid_token():
    """Decoding a garbage token must raise NotAuthorizedException."""
    from litestar.exceptions import NotAuthorizedException
    with pytest.raises(NotAuthorizedException):
        decode_jwt_token("this.is.garbage")


def test_jwt_tampered_token():
    """Modifying a valid token must fail verification."""
    from litestar.exceptions import NotAuthorizedException
    role_id = str(uuid.uuid4())
    token_str = encode_jwt_token(role_id)
    # Flip a character in the signature
    tampered = token_str[:-1] + ("A" if token_str[-1] != "A" else "B")
    with pytest.raises(NotAuthorizedException):
        decode_jwt_token(tampered)


def test_jwt_multiple_users():
    """Different users must produce different tokens."""
    t1 = encode_jwt_token("user-1")
    t2 = encode_jwt_token("user-2")
    assert t1 != t2

    d1 = decode_jwt_token(t1)
    d2 = decode_jwt_token(t2)
    assert d1.sub == "user-1"
    assert d2.sub == "user-2"
