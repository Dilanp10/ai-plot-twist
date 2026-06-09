"""InviteCode value object.

Format: ``XXXX-XXXX`` where each character is in Base32 alphabet
(A-Z plus 2-7).  The last character is a check digit:

    check = sum(ALPHABET.index(c) for c in first_7_chars) % 32
    check_char = ALPHABET[check]

This gives a single-character integrity guard that catches any
single-character substitution with 100 % probability.
"""

from __future__ import annotations

import re
import secrets as _secrets
from dataclasses import dataclass
from typing import Any

# Standard Base32 alphabet (RFC 4648), 32 symbols.
ALPHABET: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

_CODE_RE: re.Pattern[str] = re.compile(r"^[A-Z2-7]{4}-[A-Z2-7]{4}$")


@dataclass(frozen=True)
class InviteCode:
    """Immutable, validated invite-code value object.

    Always stored in canonical ``XXXX-XXXX`` upper-case form.
    Construct via :meth:`parse` or :meth:`generate`; direct
    instantiation bypasses format validation.
    """

    raw: str  # canonical "XXXX-XXXX"

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, s: str) -> InviteCode:
        """Parse a user-supplied string into a canonical ``InviteCode``.

        Accepts upper- or lower-case, with or without the separating
        hyphen.  Raises :exc:`ValueError` on any other input.
        """
        clean = s.upper().replace("-", "").replace(" ", "")
        if len(clean) != 8 or not all(c in ALPHABET for c in clean):
            raise ValueError(
                f"Código de invite inválido: {s!r}. "
                f"Debe tener 8 caracteres Base32 ({ALPHABET[:10]}…)."
            )
        return cls(raw=f"{clean[:4]}-{clean[4:]}")

    @classmethod
    def generate(cls, rng: Any = _secrets) -> InviteCode:
        """Generate a fresh, check-digit-valid ``InviteCode``.

        *rng* must expose a ``choice(sequence)`` method; defaults to the
        :mod:`secrets` module (cryptographically strong).  Pass a seeded
        :class:`random.Random` in tests for deterministic output.
        """
        data = "".join(rng.choice(ALPHABET) for _ in range(7))
        check_idx = sum(ALPHABET.index(c) for c in data) % 32
        full = data + ALPHABET[check_idx]
        return cls(raw=f"{full[:4]}-{full[4:]}")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def check_digit_valid(self) -> bool:
        """Return ``True`` iff the last character is the correct check digit.

        Any single-character substitution in the data portion will flip
        the expected check digit, so this always catches such mutations.
        """
        chars = self.raw.replace("-", "")
        data, check = chars[:7], chars[7]
        expected_idx = sum(ALPHABET.index(c) for c in data) % 32
        return ALPHABET[expected_idx] == check

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        return self.raw
