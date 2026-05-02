"""Property-based tests for server URL allowlist validation.

Feature: rust-attestated-build, Property 2: Server URL allowlist acceptance

For any server URL and any comma-separated allowlist of URLs, the server
URL SHALL be accepted if and only if it appears (after whitespace trimming)
as an exact match in the allowlist.  When the allowlist is empty, all URLs
SHALL be accepted.

**Validates: Requirements 3.4**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.validate_allowlist import validate_server_url


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A URL-like string: scheme + host + optional path.  We keep it simple —
# the allowlist logic does exact string matching, not URL parsing.
_url = st.from_regex(
    r"https?://[a-z0-9]([a-z0-9\-]{0,20}[a-z0-9])?(\.[a-z]{2,6}){1,3}(:[0-9]{1,5})?(/[a-z0-9\-._~]{0,30}){0,4}",
    fullmatch=True,
)

# Optional whitespace padding that can surround entries in the allowlist.
_padding = st.from_regex(r"[ \t]{0,4}", fullmatch=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_allowlist(urls: list[str], padding: list[str]) -> str:
    """Join *urls* into a comma-separated allowlist, applying whitespace
    padding around each entry."""
    parts: list[str] = []
    for i, url in enumerate(urls):
        pad = padding[i % len(padding)] if padding else ""
        parts.append(f"{pad}{url}{pad}")
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    server_url=_url,
    other_urls=st.lists(_url, min_size=0, max_size=5),
    padding=st.lists(_padding, min_size=1, max_size=3),
    insert_pos=st.integers(min_value=0, max_value=5),
)
def test_url_in_allowlist_is_accepted(
    server_url: str,
    other_urls: list[str],
    padding: list[str],
    insert_pos: int,
) -> None:
    """When the server URL appears in the allowlist (possibly with
    whitespace padding), ``validate_server_url`` SHALL return True.

    **Validates: Requirements 3.4**
    """
    # Insert the target URL among the other URLs.
    urls = list(other_urls)
    pos = min(insert_pos, len(urls))
    urls.insert(pos, server_url)

    allowlist = _build_allowlist(urls, padding)
    assert validate_server_url(server_url, allowlist) is True


@settings(max_examples=100)
@given(
    server_url=_url,
    other_urls=st.lists(_url, min_size=1, max_size=5),
    padding=st.lists(_padding, min_size=1, max_size=3),
)
def test_url_not_in_allowlist_is_rejected(
    server_url: str,
    other_urls: list[str],
    padding: list[str],
) -> None:
    """When the server URL does NOT appear in the allowlist,
    ``validate_server_url`` SHALL return False.

    **Validates: Requirements 3.4**
    """
    # Ensure none of the other URLs match the server URL.
    filtered = [u for u in other_urls if u != server_url]
    if not filtered:
        # All generated URLs happened to match — skip this example.
        return

    allowlist = _build_allowlist(filtered, padding)
    assert validate_server_url(server_url, allowlist) is False


@settings(max_examples=100)
@given(server_url=_url)
def test_empty_allowlist_accepts_all(server_url: str) -> None:
    """When the allowlist is empty, ``validate_server_url`` SHALL return
    True for any URL.

    **Validates: Requirements 3.4**
    """
    assert validate_server_url(server_url, "") is True
