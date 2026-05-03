"""Property-based tests for stdout marker round-trip parsing.

Feature: rust-attestated-build, Property 1: Stdout marker round-trip

For any valid marker value, embedding it in arbitrary stdout text and
parsing back SHALL return the original value.

**Validates: Requirements 2.4, 2.5, 4.2, 4.3, 4.4**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.parse_markers import parse_oci_ref_marker, parse_sha256_marker


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid SHA-256 hex digest: exactly 64 hex characters.
sha256_hex = st.text(
    alphabet="0123456789abcdefABCDEF",
    min_size=64,
    max_size=64,
)

# Surrounding text lines that do NOT accidentally contain a marker prefix.
# We filter out lines starting with "BINARY_SHA256:" or "BINARY_OCI_REF:"
# to avoid ambiguous multi-marker stdout.
_safe_line_char = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="\n\r",
    ),
    min_size=0,
    max_size=80,
).filter(
    lambda s: not s.startswith("BINARY_SHA256:")
    and not s.startswith("BINARY_OCI_REF:")
)

surrounding_lines = st.lists(_safe_line_char, min_size=0, max_size=5)

# Valid OCI reference: non-empty, no newlines, and after stripping still
# non-empty.  Also must not look like a SHA256 marker value (64 hex chars)
# to keep parsing unambiguous.  Must not start with a marker prefix.
oci_ref = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="\n\r",
    ),
    min_size=1,
    max_size=120,
).filter(
    lambda s: s.strip() != ""
    and not s.startswith("BINARY_SHA256:")
    and not s.startswith("BINARY_OCI_REF:")
)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(digest=sha256_hex, before=surrounding_lines, after=surrounding_lines)
def test_sha256_marker_roundtrip(
    digest: str,
    before: list[str],
    after: list[str],
) -> None:
    """Embedding a BINARY_SHA256 marker in arbitrary stdout and parsing it
    back SHALL return the original hex digest (lowercased).

    **Validates: Requirements 2.4, 4.2, 4.3, 4.4**
    """
    marker_line = f"BINARY_SHA256:{digest}"
    stdout = "\n".join([*before, marker_line, *after])
    result = parse_sha256_marker(stdout)
    assert result == digest.lower()


@settings(max_examples=100)
@given(ref=oci_ref, before=surrounding_lines, after=surrounding_lines)
def test_oci_ref_marker_roundtrip(
    ref: str,
    before: list[str],
    after: list[str],
) -> None:
    """Embedding a BINARY_OCI_REF marker in arbitrary stdout and
    parsing it back SHALL return the original value (stripped).

    **Validates: Requirements 2.5, 4.2, 4.3, 4.4**
    """
    marker_line = f"BINARY_OCI_REF:{ref}"
    stdout = "\n".join([*before, marker_line, *after])
    result = parse_oci_ref_marker(stdout)
    assert result == ref.strip()
