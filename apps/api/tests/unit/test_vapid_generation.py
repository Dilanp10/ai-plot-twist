"""Unit tests: generate-vapid CLI (T-002).

Coverage:
  1. ``generate_keys()`` returns a PKCS#8 PEM private key and a 65-byte
     base64-url public key (no padding).
  2. The private + public keys are mathematically paired — signing
     with the private verifies with the public.
  3. ``--seed`` produces deterministic output across runs (test only).
  4. ``--seed`` with different values produces different output.
  5. ``--out`` refuses overwrite when the file already exists.
  6. ``_resolve_admin_token`` style: parser defaults / explicit overrides.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from app.scripts.generate_vapid import (
    _format_keys,
    _write_out_or_exit,
    build_parser,
    generate_keys,
)


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.out is None
    assert args.seed is None


def test_parser_accepts_seed_and_out(tmp_path: Path) -> None:
    out = tmp_path / "k.txt"
    args = build_parser().parse_args(["--out", str(out), "--seed", "42"])
    assert args.out == str(out)
    assert args.seed == 42


def test_generate_keys_returns_pkcs8_pem_and_b64url_public() -> None:
    priv_pem, pub_b64url = generate_keys()

    assert priv_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert priv_pem.rstrip().endswith("-----END PRIVATE KEY-----")

    # Base64-url, no padding.
    assert "=" not in pub_b64url
    assert "+" not in pub_b64url
    assert "/" not in pub_b64url

    # Decode: 65-byte uncompressed P-256 point — leading 0x04 prefix.
    raw = base64.urlsafe_b64decode(pub_b64url + "==")
    assert len(raw) == 65
    assert raw[0] == 0x04


def test_generated_keys_are_paired() -> None:
    """Signing with the PEM private key MUST verify with the b64 public."""
    priv_pem, pub_b64url = generate_keys()

    private = serialization.load_pem_private_key(
        priv_pem.encode("ascii"), password=None
    )
    assert isinstance(private, ec.EllipticCurvePrivateKey)

    raw_pub = base64.urlsafe_b64decode(pub_b64url + "==")
    public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), raw_pub
    )

    signature = private.sign(b"hello vapid", ec.ECDSA(hashes.SHA256()))
    # Round-trip the signature through DSS encode/decode to confirm it's
    # a valid (r, s) pair — a structural sanity check on the curve params.
    r, s = decode_dss_signature(signature)
    assert encode_dss_signature(r, s) == signature

    # Verifying must NOT raise.
    public.verify(signature, b"hello vapid", ec.ECDSA(hashes.SHA256()))


def test_seed_is_deterministic() -> None:
    """Same seed → same keypair, twice."""
    a = generate_keys(seed=12345)
    b = generate_keys(seed=12345)
    assert a == b


def test_different_seeds_produce_different_keys() -> None:
    a = generate_keys(seed=12345)
    b = generate_keys(seed=12346)
    assert a != b


def test_format_keys_emits_both_env_lines() -> None:
    text = _format_keys("-----PRIV PEM-----", "PUB-B64-PLACEHOLDER")
    assert "VAPID_PUBLIC_KEY=PUB-B64-PLACEHOLDER" in text
    assert "VAPID_PRIVATE_KEY=-----PRIV PEM-----" in text


def test_write_out_refuses_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "keys.txt"
    out.write_text("pre-existing")
    with pytest.raises(SystemExit) as exc:
        _write_out_or_exit("new content", out)
    assert exc.value.code == 1
    # File untouched.
    assert out.read_text() == "pre-existing"


def test_write_out_creates_file_when_absent(tmp_path: Path) -> None:
    out = tmp_path / "keys.txt"
    _write_out_or_exit("contents-here", out)
    assert out.exists()
    assert out.read_text() == "contents-here"
