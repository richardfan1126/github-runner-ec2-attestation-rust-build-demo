"""Parser for stdout markers emitted by the build script.

Extracts ``BINARY_SHA256:<hex_digest>`` and ``BINARY_ARTIFACT_NAME:<name>``
markers from arbitrary stdout text produced by the Remote Executor build
script.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Marker patterns
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^BINARY_SHA256:([0-9a-fA-F]{64})$", re.MULTILINE)
_ARTIFACT_NAME_RE = re.compile(r"^BINARY_ARTIFACT_NAME:(.+)$", re.MULTILINE)


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


def parse_artifact_name_marker(stdout: str) -> str:
    """Extract the ``BINARY_ARTIFACT_NAME`` value from build stdout.

    Parameters
    ----------
    stdout:
        The full stdout text captured from the build script execution.

    Returns
    -------
    str
        The artifact name (non-empty string without newlines).

    Raises
    ------
    ValueError
        If the ``BINARY_ARTIFACT_NAME`` marker is missing or the value is
        empty.
    """
    match = _ARTIFACT_NAME_RE.search(stdout)
    if match is None:
        raise ValueError("BINARY_ARTIFACT_NAME marker not found in stdout")
    name = match.group(1).strip()
    if not name:
        raise ValueError(
            "BINARY_ARTIFACT_NAME marker found but value is empty"
        )
    return name
