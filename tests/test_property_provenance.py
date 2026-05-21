"""Property-based tests for provenance manifest completeness.

Feature: rust-attestated-build, Property 3: Provenance manifest completeness

For any valid build metadata inputs, the generated manifest SHALL contain
all values in correct fields, and parsing back yields originals.

**Validates: Requirements 5.2**
"""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.create_provenance import create_provenance_manifest


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-empty text strings for binary name.
binary_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=120,
).filter(lambda s: s.strip() != "")

# 64-character hex strings (SHA-256 digest).
sha256_hex = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# URL-like strings for repository URL.
repo_urls = st.from_regex(
    r"https://[a-z0-9]([a-z0-9\-]{0,20}[a-z0-9])?(\.[a-z]{2,6}){1,3}/[a-z0-9\-._]{1,30}/[a-z0-9\-._]{1,30}",
    fullmatch=True,
)

# 40-character hex strings (git SHA).
commit_hashes = st.text(
    alphabet="0123456789abcdef",
    min_size=40,
    max_size=40,
)

# Numeric strings for workflow run ID.
run_ids = st.from_regex(r"[1-9][0-9]{0,18}", fullmatch=True)

# ISO 8601 datetime strings.
timestamps = st.datetimes().map(lambda dt: dt.isoformat())


# ---------------------------------------------------------------------------
# Expected fixed attestation paths
# ---------------------------------------------------------------------------

_EXPECTED_ATTESTATION = {
    "server_identity": "attestation-documents/server-identity.b64",
    "execution_acceptance": "attestation-documents/execution-acceptance.b64",
    "output_integrity": "attestation-documents/output-integrity.b64",
    "manifest": "attestation-documents/manifest.json",
}


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    binary_name=binary_names,
    sha256=sha256_hex,
    repo_url=repo_urls,
    commit_hash=commit_hashes,
    run_id=run_ids,
    timestamp=timestamps,
)
def test_provenance_manifest_completeness(
    binary_name: str,
    sha256: str,
    repo_url: str,
    commit_hash: str,
    run_id: str,
    timestamp: str,
) -> None:
    """For any valid build metadata inputs, the generated provenance manifest
    SHALL contain all values in the correct fields, and a JSON round-trip
    SHALL preserve every value.

    **Validates: Requirements 5.2**
    """
    manifest = create_provenance_manifest(
        binary_name=binary_name,
        sha256=sha256,
        repo_url=repo_url,
        commit_hash=commit_hash,
        run_id=run_id,
        timestamp=timestamp,
    )

    # 1. Version is "1.0"
    assert manifest["version"] == "1.0"

    # 2. Binary name matches input
    assert manifest["binary"]["name"] == binary_name

    # 3. Binary sha256 matches input
    assert manifest["binary"]["sha256"] == sha256

    # 4. Build repository_url matches input
    assert manifest["build"]["repository_url"] == repo_url

    # 5. Build commit_hash matches input
    assert manifest["build"]["commit_hash"] == commit_hash

    # 6. Build workflow_run_id matches input
    assert manifest["build"]["workflow_run_id"] == run_id

    # 7. Build timestamp matches input
    assert manifest["build"]["timestamp"] == timestamp

    # 8. Attestation section has the fixed paths
    assert manifest["attestation"] == _EXPECTED_ATTESTATION

    # 9. JSON round-trip preserves all values
    serialized = json.dumps(manifest)
    deserialized = json.loads(serialized)
    assert deserialized == manifest
