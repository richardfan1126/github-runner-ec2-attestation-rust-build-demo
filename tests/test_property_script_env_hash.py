"""Property-based tests for script_env_hash round-trip.

Feature: rust-attestated-build, Property 4: script_env_hash round-trip

For any dictionary of string key-value pairs, the hash is deterministic
and matches the canonical algorithm.

**Validates: Requirements 10.6, 10.7**
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# Ensure the caller module is importable
_scripts_dir = str(Path(__file__).resolve().parent.parent / ".github" / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from call_remote_executor.caller import RemoteExecutorCaller


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Dictionary of non-empty string keys to arbitrary string values.
# Keys must be non-empty (min_size=1) to be valid JSON object keys.
string_dicts = st.dictionaries(
    keys=st.text(min_size=1),
    values=st.text(),
)


# ---------------------------------------------------------------------------
# Reference implementation of the canonical algorithm
# ---------------------------------------------------------------------------


def _reference_hash(d: dict[str, str] | None) -> str:
    """Reference implementation of the script_env_hash algorithm.

    Canonicalization: sort keys lexicographically, JSON with compact
    separators (',', ':'), no whitespace. SHA-256 hex digest.
    When d is None or empty, compute hash of "{}".
    """
    if not d:
        canonical = json.dumps({}, sort_keys=True, separators=(",", ":"))
    else:
        canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(d=string_dicts)
def test_script_env_hash_deterministic(d: dict[str, str]) -> None:
    """Calling _compute_script_env_hash twice with the same dict produces
    the same hash.

    **Validates: Requirements 10.6, 10.7**
    """
    hash1 = RemoteExecutorCaller._compute_script_env_hash(d)
    hash2 = RemoteExecutorCaller._compute_script_env_hash(d)
    assert hash1 == hash2


@settings(max_examples=100)
@given(d=string_dicts)
def test_script_env_hash_matches_canonical_algorithm(d: dict[str, str]) -> None:
    """The result of _compute_script_env_hash matches the canonical algorithm:
    hashlib.sha256(json.dumps(d, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    **Validates: Requirements 10.6, 10.7**
    """
    result = RemoteExecutorCaller._compute_script_env_hash(d)
    expected = _reference_hash(d)
    assert result == expected


@settings(max_examples=100)
@given(d=string_dicts)
def test_script_env_hash_key_order_independence(d: dict[str, str]) -> None:
    """For any dict, reversing the key insertion order produces the same hash.
    This verifies key-order independence.

    **Validates: Requirements 10.6, 10.7**
    """
    # Create a dict with reversed key order
    reversed_d = dict(reversed(list(d.items())))
    hash_original = RemoteExecutorCaller._compute_script_env_hash(d)
    hash_reversed = RemoteExecutorCaller._compute_script_env_hash(reversed_d)
    assert hash_original == hash_reversed


def test_script_env_hash_empty_dict_matches_sha256_empty_json() -> None:
    """Empty dict produces sha256("{}").

    **Validates: Requirements 10.6, 10.7**
    """
    expected = hashlib.sha256(b"{}").hexdigest()
    assert RemoteExecutorCaller._compute_script_env_hash({}) == expected


def test_script_env_hash_none_matches_sha256_empty_json() -> None:
    """None produces sha256("{}").

    **Validates: Requirements 10.6, 10.7**
    """
    expected = hashlib.sha256(b"{}").hexdigest()
    assert RemoteExecutorCaller._compute_script_env_hash(None) == expected
