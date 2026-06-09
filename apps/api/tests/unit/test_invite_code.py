"""Unit tests: InviteCode value object.

Module 002 / Task T-004.

Coverage targets:
  - parse() roundtrip: various formats → same canonical object
  - parse() rejection: invalid inputs raise ValueError
  - mutation rejection: any data-char change invalidates check digit
  - deterministic generation: fixed-seed RNG → reproducible code
  - bulk validity: 10 000 random codes all pass check_digit_valid()
"""

from __future__ import annotations

import random

import pytest

from app.domain.invites import ALPHABET, InviteCode

# ---------------------------------------------------------------------------
# parse() — roundtrip
# ---------------------------------------------------------------------------


def test_parse_canonical_unchanged() -> None:
    code = InviteCode.parse("AAAA-AAAB")
    assert code.raw == "AAAA-AAAB"


def test_parse_lowercase_normalised() -> None:
    assert InviteCode.parse("aaaa-aaab") == InviteCode.parse("AAAA-AAAB")


def test_parse_no_hyphen() -> None:
    assert InviteCode.parse("AAAAAAAB") == InviteCode.parse("AAAA-AAAB")


def test_parse_lowercase_no_hyphen() -> None:
    assert InviteCode.parse("aaaaaaab") == InviteCode.parse("AAAA-AAAB")


def test_parse_str_representation() -> None:
    code = InviteCode.parse("AAAA-AAAB")
    assert str(code) == "AAAA-AAAB"


# ---------------------------------------------------------------------------
# parse() — rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_input",
    [
        "",            # empty
        "AAAA",        # too short
        "AAAA-AAAAB",  # too long (9 data chars)
        "AAAA-AAA1",   # invalid char '1' (not in Base32)
        "AAAA-AAA0",   # invalid char '0'
        "AAAA-AAA8",   # invalid char '8'
        "AAAA-AAA9",   # invalid char '9'
        "AAAA-A",      # too short after stripping hyphen
    ],
)
def test_parse_invalid_raises(bad_input: str) -> None:
    with pytest.raises(ValueError):
        InviteCode.parse(bad_input)


# ---------------------------------------------------------------------------
# check_digit_valid()
# ---------------------------------------------------------------------------


def test_generated_code_has_valid_check_digit() -> None:
    code = InviteCode.generate()
    assert code.check_digit_valid()


def test_mutation_of_data_char_invalidates_check_digit() -> None:
    """Changing any of the 7 data characters must invalidate the check digit."""
    code = InviteCode.generate()
    chars = code.raw.replace("-", "")

    for pos in range(7):
        original = chars[pos]
        # Pick a different character in the same alphabet
        mutated_char = next(c for c in ALPHABET if c != original)
        mutated_chars = list(chars)
        mutated_chars[pos] = mutated_char
        mutated_raw = f"{''.join(mutated_chars[:4])}-{''.join(mutated_chars[4:])}"
        mutated_code = InviteCode(raw=mutated_raw)
        assert not mutated_code.check_digit_valid(), (
            f"Mutación en posición {pos} ({original!r}→{mutated_char!r}) "
            f"debería invalidar el check digit del código {code.raw!r}"
        )


def test_mutation_of_check_digit_invalidates() -> None:
    """Changing the check digit (pos 7) must also fail."""
    code = InviteCode.generate()
    chars = code.raw.replace("-", "")
    original = chars[7]
    bad_check = next(c for c in ALPHABET if c != original)
    mutated_raw = f"{chars[:4]}-{chars[4:7]}{bad_check}"
    assert not InviteCode(raw=mutated_raw).check_digit_valid()


# ---------------------------------------------------------------------------
# generate() — deterministic under fixed RNG
# ---------------------------------------------------------------------------


def test_generate_deterministic_with_fixed_seed() -> None:
    rng1 = random.Random(42)
    rng2 = random.Random(42)
    assert InviteCode.generate(rng=rng1) == InviteCode.generate(rng=rng2)


def test_generate_different_seeds_differ() -> None:
    # Astronomically unlikely to collide with different seeds
    code_a = InviteCode.generate(rng=random.Random(1))
    code_b = InviteCode.generate(rng=random.Random(2))
    assert code_a != code_b


# ---------------------------------------------------------------------------
# bulk validity
# ---------------------------------------------------------------------------


def test_10000_random_generations_all_valid() -> None:
    """All generated codes must pass check_digit_valid()."""
    for _ in range(10_000):
        assert InviteCode.generate().check_digit_valid()
