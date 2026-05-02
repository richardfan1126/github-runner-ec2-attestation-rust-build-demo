"""Server URL allowlist validation.

Validates a server URL against a comma-separated allowlist of permitted
URLs.  When the allowlist is empty every URL is accepted; otherwise the
URL must appear as an exact match (after whitespace trimming) in the
list.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_server_url(server_url: str, allowlist: str) -> bool:
    """Check whether *server_url* is permitted by *allowlist*.

    Parameters
    ----------
    server_url:
        The URL to validate.
    allowlist:
        A comma-separated string of allowed URLs.  Whitespace around
        individual entries is stripped before comparison.  An empty
        string (or a string containing only whitespace/commas) means
        "allow all".

    Returns
    -------
    bool
        ``True`` if the URL is accepted, ``False`` if rejected.
    """
    entries = [entry.strip() for entry in allowlist.split(",")]
    # Filter out empty entries that result from leading/trailing commas
    # or an entirely empty allowlist string.
    entries = [e for e in entries if e]

    if not entries:
        return True

    return server_url in entries
