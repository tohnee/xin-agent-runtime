# -*- coding: utf-8 -*-
"""G2: JwtClaimsParser must enforce nbf/iat/aud/iss with clock-skew
leeway, not just exp.

The previous implementation only validated ``exp`` (expiry). RFC 7519
defines several other time-based and claim-based checks that must be
enforced when the corresponding claim is present or when the
verifier is configured with an expected audience/issuer:

- ``nbf`` (not-before): reject tokens whose ``nbf`` is in the future.
- ``iat`` (issued-at): reject tokens whose ``iat`` is in the future
  (a token issued in the future is a sign of tampering or clock
  skew).
- ``aud`` (audience): when the parser is configured with
  ``expected_audience``, the token's ``aud`` claim must match.
- ``iss`` (issuer): when the parser is configured with
  ``expected_issuer``, the token's ``iss`` claim must match.
- ``leeway``: small clock-skew tolerance (default 0 seconds) applied
  to ``exp`` and ``nbf`` checks.
"""
import base64
import hashlib
import hmac
import json
import time

import pytest

from xruntime._runtime._tenant._store import JwtClaimsParser


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _signed_jwt(payload: dict, secret: str, alg: str = "HS256") -> str:
    """Create a signed JWT for testing."""
    header = {"alg": alg, "typ": "JWT"}
    header_b64 = _b64url(header)
    payload_b64 = _b64url(payload)
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{header_b64}.{payload_b64}.{sig_b64}"


SECRET = "test-secret-key"


def _base_payload(**overrides) -> dict:
    """Payload with valid defaults: tenant_id, sub, role, exp."""
    payload = {
        "tenant_id": "acme",
        "sub": "alice",
        "role": "viewer",
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return payload


class TestJwtNbfValidation:
    """``nbf`` (not-before) must be enforced."""

    def test_rejects_nbf_in_future(self) -> None:
        """Token with nbf > now must be rejected."""
        token = _signed_jwt(
            _base_payload(nbf=int(time.time()) + 3600),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET)
        with pytest.raises(ValueError, match="nbf|not-before|future"):
            parser.parse(token)

    def test_accepts_nbf_in_past(self) -> None:
        """Token with nbf < now must be accepted."""
        token = _signed_jwt(
            _base_payload(nbf=int(time.time()) - 60),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET)
        principal = parser.parse(token)
        assert principal.user_id == "alice"

    def test_nbf_within_leeway_accepted(self) -> None:
        """nbf slightly in the future but within leeway → accept."""
        token = _signed_jwt(
            _base_payload(nbf=int(time.time()) + 5),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET, leeway=10)
        principal = parser.parse(token)
        assert principal.user_id == "alice"


class TestJwtIatValidation:
    """``iat`` (issued-at) must be enforced for future-dated tokens."""

    def test_rejects_iat_in_future(self) -> None:
        """Token with iat > now+leeway must be rejected (tamper/sign)."""
        token = _signed_jwt(
            _base_payload(iat=int(time.time()) + 3600),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET)
        with pytest.raises(ValueError, match="iat|issued-at|future"):
            parser.parse(token)

    def test_accepts_iat_in_past(self) -> None:
        """Token with iat < now must be accepted."""
        token = _signed_jwt(
            _base_payload(iat=int(time.time()) - 60),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET)
        principal = parser.parse(token)
        assert principal.user_id == "alice"

    def test_iat_within_leeway_accepted(self) -> None:
        """iat slightly in the future but within leeway → accept."""
        token = _signed_jwt(
            _base_payload(iat=int(time.time()) + 5),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET, leeway=10)
        principal = parser.parse(token)
        assert principal.user_id == "alice"


class TestJwtAudienceValidation:
    """``aud`` validation must be enforced when expected_audience
    is configured."""

    def test_rejects_wrong_audience(self) -> None:
        """Token aud != expected_audience → reject."""
        token = _signed_jwt(
            _base_payload(aud="wrong-audience"),
            SECRET,
        )
        parser = JwtClaimsParser(
            secret=SECRET,
            expected_audience="xruntime-api",
        )
        with pytest.raises(ValueError, match="aud|audience"):
            parser.parse(token)

    def test_accepts_correct_audience(self) -> None:
        """Token aud == expected_audience → accept."""
        token = _signed_jwt(
            _base_payload(aud="xruntime-api"),
            SECRET,
        )
        parser = JwtClaimsParser(
            secret=SECRET,
            expected_audience="xruntime-api",
        )
        principal = parser.parse(token)
        assert principal.user_id == "alice"

    def test_no_audience_claim_rejected_when_expected_set(self) -> None:
        """Token without aud claim → reject when expected_audience
        is configured."""
        token = _signed_jwt(_base_payload(), SECRET)
        parser = JwtClaimsParser(
            secret=SECRET,
            expected_audience="xruntime-api",
        )
        with pytest.raises(ValueError, match="aud|audience"):
            parser.parse(token)

    def test_no_expected_audience_skips_check(self) -> None:
        """When expected_audience is None, aud check is skipped."""
        token = _signed_jwt(
            _base_payload(aud="anything"),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET)
        principal = parser.parse(token)
        assert principal.user_id == "alice"


class TestJwtIssuerValidation:
    """``iss`` validation must be enforced when expected_issuer
    is configured."""

    def test_rejects_wrong_issuer(self) -> None:
        """Token iss != expected_issuer → reject."""
        token = _signed_jwt(
            _base_payload(iss="wrong-issuer"),
            SECRET,
        )
        parser = JwtClaimsParser(
            secret=SECRET,
            expected_issuer="https://auth.example.com",
        )
        with pytest.raises(ValueError, match="iss|issuer"):
            parser.parse(token)

    def test_accepts_correct_issuer(self) -> None:
        """Token iss == expected_issuer → accept."""
        token = _signed_jwt(
            _base_payload(iss="https://auth.example.com"),
            SECRET,
        )
        parser = JwtClaimsParser(
            secret=SECRET,
            expected_issuer="https://auth.example.com",
        )
        principal = parser.parse(token)
        assert principal.user_id == "alice"

    def test_no_issuer_claim_rejected_when_expected_set(self) -> None:
        """Token without iss claim → reject when expected_issuer
        is configured."""
        token = _signed_jwt(_base_payload(), SECRET)
        parser = JwtClaimsParser(
            secret=SECRET,
            expected_issuer="https://auth.example.com",
        )
        with pytest.raises(ValueError, match="iss|issuer"):
            parser.parse(token)

    def test_no_expected_issuer_skips_check(self) -> None:
        """When expected_issuer is None, iss check is skipped."""
        token = _signed_jwt(
            _base_payload(iss="anything"),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET)
        principal = parser.parse(token)
        assert principal.user_id == "alice"


class TestJwtExpLeeway:
    """``exp`` must respect the leeway parameter for clock skew."""

    def test_exp_within_leeway_accepted(self) -> None:
        """exp slightly past but within leeway → accept."""
        token = _signed_jwt(
            _base_payload(exp=int(time.time()) - 5),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET, leeway=10)
        principal = parser.parse(token)
        assert principal.user_id == "alice"

    def test_exp_beyond_leeway_rejected(self) -> None:
        """exp past and beyond leeway → reject."""
        token = _signed_jwt(
            _base_payload(exp=int(time.time()) - 3600),
            SECRET,
        )
        parser = JwtClaimsParser(secret=SECRET, leeway=10)
        with pytest.raises(ValueError, match="exp|expired"):
            parser.parse(token)
