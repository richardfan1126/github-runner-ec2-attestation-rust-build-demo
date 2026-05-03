"""Parser for stdout markers emitted by the build script.

Extracts ``BINARY_SHA256:<hex_digest>`` and ``BINARY_OCI_REF:<reference>``
markers from arbitrary stdout text produced by the Remote Executor build
script.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Marker patterns
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^BINARY_SHA256:([0-9a-fA-F]{64})$", re.MULTILINE)
_OCI_REF_RE = re.compile(r"^BINARY_OCI_REF:(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_sha256_marker(stdout: str) -> str:
    """Extract the ``BINARY_SHA256`` value from build stdout.

    Parameters
    ----------
    stdout:
        The full stdout text captured from the build script execution.

    Returns
    -------
    str
        The 64-character lowercase hex SHA-256 digest.

    Raises
    ------
    ValueError
        If the ``BINARY_SHA256`` marker is missing or the value is not a
        valid 64-character hex string.
    """
    match = _SHA256_RE.search(stdout)
    if match is None:
        # Check if the marker prefix exists but with a malformed value
        if re.search(r"^BINARY_SHA256:", stdout, re.MULTILINE):
            raise ValueError(
                "BINARY_SHA256 marker found but value is not a valid "
                "64-character hex string"
            )
        raise ValueError("BINARY_SHA256 marker not found in stdout")
    return match.group(1).lower()


def parse_oci_ref_marker(stdout: str) -> str:
    """Extract the ``BINARY_OCI_REF`` value from build stdout.

    Parameters
    ----------
    stdout:
        The full stdout text captured from the build script execution.

    Returns
    -------
    str
        The OCI reference (non-empty string without newlines), e.g.
        ``ghcr.io/owner/repo/tmp-build:abc1234-x7k9m2``.

    Raises
    ------
    ValueError
        If the ``BINARY_OCI_REF`` marker is missing or the value is
        empty.
    """
    match = _OCI_REF_RE.search(stdout)
    if match is None:
        raise ValueError("BINARY_OCI_REF marker not found in stdout")
    ref = match.group(1).strip()
    if not ref:
        raise ValueError(
            "BINARY_OCI_REF marker found but value is empty"
        )
    return ref
