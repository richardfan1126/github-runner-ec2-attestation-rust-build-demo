"""Provenance manifest generation.

Creates a JSON-serializable provenance manifest that ties together the
built binary, attestation chain, and build metadata.  The manifest
schema follows the design document specification (version 1.0).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MANIFEST_VERSION = "1.0"

_ATTESTATION_PATHS = {
    "server_identity": "attestation-documents/server-identity.b64",
    "execution_acceptance": "attestation-documents/execution-acceptance.b64",
    "output_integrity": "attestation-documents/output-integrity-poll-001.b64",
    "manifest": "attestation-documents/manifest.json",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_provenance_manifest(
    binary_name: str,
    sha256: str,
    repo_url: str,
    commit_hash: str,
    run_id: str,
    timestamp: str,
) -> dict:
    """Build a provenance manifest dict for the attested binary.

    Parameters
    ----------
    binary_name:
        Name of the compiled binary (e.g. ``"attested-hello"``).
    sha256:
        Hex-encoded SHA-256 digest of the binary.
    repo_url:
        Repository URL (e.g. ``"https://github.com/owner/repo"``).
    commit_hash:
        Git commit SHA the binary was built from.
    run_id:
        GitHub Actions workflow run ID.
    timestamp:
        ISO 8601 build timestamp.

    Returns
    -------
    dict
        A JSON-serializable dictionary matching the provenance manifest
        schema defined in the design document.
    """
    return {
        "version": _MANIFEST_VERSION,
        "binary": {
            "name": binary_name,
            "sha256": sha256,
        },
        "build": {
            "repository_url": repo_url,
            "commit_hash": commit_hash,
            "workflow_run_id": run_id,
            "timestamp": timestamp,
        },
        "attestation": dict(_ATTESTATION_PATHS),
    }
