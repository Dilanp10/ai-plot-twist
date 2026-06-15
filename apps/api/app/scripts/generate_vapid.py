"""CLI: generate-vapid — print a fresh VAPID keypair for Web Push.

Module 011 / Task T-002.

VAPID (RFC 8292) keys are P-256 ECDSA. We produce two encodings:

  - **Private key**: PEM (PKCS#8). This is what
    :class:`app.infra.webpush_sender.WebPushSender` consumes — set it
    on Fly as ``VAPID_PRIVATE_KEY`` (single line with literal ``\\n``
    separators when configuring via `fly secrets set`).
  - **Public key**: 65-byte raw uncompressed P-256 point, base64-url
    encoded with no padding. This is the *exact* string the PWA passes
    to ``PushManager.subscribe({ applicationServerKey })`` — set it on
    the API as ``VAPID_PUBLIC_KEY`` and expose it via
    ``GET /push/public-key`` (T-007).

Usage::

    pnpm generate-vapid                 # print to stdout
    pnpm generate-vapid --out keys.txt  # append to file (refuses overwrite)
    pnpm generate-vapid --seed 7        # deterministic (TEST ONLY)

Exit codes:
    0  keys printed / saved.
    1  --out file already exists and would be overwritten.
    2  argparse usage error.

Security: the private key MUST stay secret. The CLI prints both keys
on stdout for convenience in dev; in CI / prod, redirect to a sealed
secret store and never check the private key into git.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate-vapid",
        description=(
            "Genera un par de claves VAPID (P-256) para Web Push. "
            "Imprime PEM (privada) y base64-url-65-bytes (pública)."
        ),
    )
    p.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help=(
            "Si se provee, escribe ambas claves a FILE. "
            "Rechaza el overwrite (exit 1)."
        ),
    )
    p.add_argument(
        "--seed",
        default=None,
        type=int,
        metavar="N",
        help=(
            "Solo para tests: genera una clave determinística usando "
            "la semilla N. NO USAR EN PROD."
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def _private_key_from_seed(seed: int) -> ec.EllipticCurvePrivateKey:
    """Deterministic ECDSA P-256 key — TEST ONLY.

    Builds a scalar in [1, n-1] from the integer seed and uses it as
    the private value. NIST P-256 order (n) is the SECP256R1 order
    embedded in cryptography; we read it from the curve.
    """
    n = int(
        "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551",
        16,
    )
    raw = (seed % (n - 1)) + 1  # ensure 1 ≤ raw ≤ n-1
    return ec.derive_private_key(raw, ec.SECP256R1())


def generate_keys(*, seed: int | None = None) -> tuple[str, str]:
    """Return ``(private_pem, public_b64url)``.

    The public encoding is 65-byte uncompressed (``\\x04 || X || Y``)
    base64-url-encoded without padding — the format the browser's
    ``applicationServerKey`` expects.
    """
    private = (
        _private_key_from_seed(seed)
        if seed is not None
        else ec.generate_private_key(ec.SECP256R1())
    )
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64url = (
        base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("ascii")
    )
    return private_pem, public_b64url


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _format_keys(private_pem: str, public_b64url: str) -> str:
    return (
        "# VAPID keypair (P-256). KEEP THE PRIVATE KEY SECRET.\n"
        "# Public key — paste into VAPID_PUBLIC_KEY (served by GET /push/public-key).\n"
        f"VAPID_PUBLIC_KEY={public_b64url}\n\n"
        "# Private key (PEM) — paste into VAPID_PRIVATE_KEY.\n"
        f"VAPID_PRIVATE_KEY={private_pem}\n"
    )


def _write_out_or_exit(text: str, out_path: Path) -> None:
    if out_path.exists():
        print(
            f"ERROR: {out_path} ya existe. Eliminálo a mano si querés "
            "sobreescribirlo (no autorizo overwrite silencioso).",
            file=sys.stderr,
        )
        sys.exit(1)
    # Use 0o600 so the file is owner-read/write only by default — matches
    # the sensitivity of the embedded private key.
    fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = build_parser().parse_args()
    priv_pem, pub_b64url = generate_keys(seed=args.seed)
    text = _format_keys(priv_pem, pub_b64url)
    if args.out is not None:
        _write_out_or_exit(text, Path(args.out))
        print(f"Claves escritas a {args.out} (chmod 600).")
        return
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
