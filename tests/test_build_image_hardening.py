"""Static (text-inspection) tests for the hardened build image and script.

Feature: 001-hardened-executor-build (Hardened-Executor-Compatible Attested
Build).

This module statically inspects the *text* of two checked-in artifacts —
the repo-root ``Dockerfile`` and ``scripts/build-rust.sh`` — to assert the
supply-chain and runtime-hardening invariants. It deliberately performs no
Docker/container work: container behavior is validated by the manual
``specs/001-hardened-executor-build/quickstart.md``. The style mirrors
``tests/test_security_hardening.py`` (path + text inspection).

This file is the shared scaffolding (T002, Phase 2 — Foundational). It
provides the lazy loaders and fixtures that the per-story assertion tasks
extend:

* US1 (T003) — assertions over ``scripts/build-rust.sh`` text.
* US2 (T008) — assertions over ``Dockerfile`` text.

No assertions live here yet — only the loaders/fixtures.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Artifact locations (repository-relative from the repo root)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
BUILD_SCRIPT_PATH = REPO_ROOT / "scripts" / "build-rust.sh"


# ---------------------------------------------------------------------------
# Lazy text loaders
# ---------------------------------------------------------------------------
#
# Loaders are functions (not import-time reads) so that test *collection*
# never fails when an artifact does not yet exist — e.g. the Dockerfile is
# created later by T009. A missing artifact yields ``pytest.skip`` at fixture
# time, so script tests (US1) run while Dockerfile tests (US2) skip until the
# Dockerfile lands, rather than erroring the whole module.


def _read_text(path: Path, label: str) -> str:
    """Return the raw UTF-8 text of ``path``; skip the test if it is absent."""
    if not path.exists():
        pytest.skip(f"{label} not present yet at {path}")
    return path.read_text(encoding="utf-8")


def read_dockerfile() -> str:
    """Return the raw text of the repo-root ``Dockerfile``."""
    return _read_text(DOCKERFILE_PATH, "Dockerfile")


def read_build_script() -> str:
    """Return the raw text of ``scripts/build-rust.sh``."""
    return _read_text(BUILD_SCRIPT_PATH, "scripts/build-rust.sh")


# ---------------------------------------------------------------------------
# Fixtures consumed by the per-story assertion tasks (T003 / T008)
# ---------------------------------------------------------------------------


@pytest.fixture
def dockerfile_text() -> str:
    """Raw text of the repo-root ``Dockerfile`` (skips if not yet created)."""
    return read_dockerfile()


@pytest.fixture
def build_script_text() -> str:
    """Raw text of ``scripts/build-rust.sh`` (skips if not yet created)."""
    return read_build_script()


# ===========================================================================
# US1 (T003) — static assertions over ``scripts/build-rust.sh``
# ===========================================================================
#
# These inspect the *text* of the hardened build script. They trace the
# runtime-hardening invariants for User Story 1 (FR-003, FR-010, FR-011,
# FR-016): the script runs scratch-only and install-free, sets cargo state
# under the scratch root while keeping RUSTUP_HOME on the read-only image
# toolchain, preflights its tools, write-probes scratch, and emits the
# success markers only after the oras push.


# Package-manager install invocations (apt-get/apt/dnf/yum/apk … install|add).
_PKG_INSTALL_RE = re.compile(
    r"\b(?:apt-get|apt|dnf|yum|apk)\b[^\n]*\b(?:install|add)\b"
)

# Run-time rustup download/install (the legacy ``sh.rustup.rs`` / rustup-init path).
_RUSTUP_DOWNLOAD_RE = re.compile(r"sh\.rustup\.rs|rustup-init|rustup\.rs")


def test_build_script_has_no_runtime_package_install(build_script_text: str) -> None:
    """No apt-get/dnf/yum/apk install at run time (FR-003, FR-011)."""
    match = _PKG_INSTALL_RE.search(build_script_text)
    assert match is None, (
        "build-rust.sh must perform NO run-time package install; "
        f"found: {match.group(0)!r}"
    )


def test_build_script_has_no_rustup_download(build_script_text: str) -> None:
    """No run-time rustup toolchain download/install (FR-003, FR-011)."""
    match = _RUSTUP_DOWNLOAD_RE.search(build_script_text)
    assert match is None, (
        "build-rust.sh must NOT download/install the Rust toolchain at run time "
        f"(toolchain ships in the image); found: {match.group(0)!r}"
    )


def test_build_script_cargo_state_under_scratch(build_script_text: str) -> None:
    """CARGO_HOME and CARGO_TARGET_DIR are rooted under the scratch dir (FR-010)."""
    # Scratch root resolves from BUILD_SCRATCH_DIR with a /tmp default.
    assert re.search(
        r'SCRATCH_DIR="?\$\{BUILD_SCRATCH_DIR:-/tmp\}"?', build_script_text
    ), "scratch root must resolve from ${BUILD_SCRATCH_DIR:-/tmp}"

    # CARGO_HOME / CARGO_TARGET_DIR are defined relative to the scratch root,
    # not to $HOME or a read-only path.
    cargo_home = re.search(r'CARGO_HOME="?\$\{?SCRATCH_DIR\}?', build_script_text)
    assert cargo_home, "CARGO_HOME must be set beneath ${SCRATCH_DIR}"

    cargo_target = re.search(
        r'CARGO_TARGET_DIR="?\$\{?(?:SCRATCH_DIR|TMP_RUST_PROJECT)\}?',
        build_script_text,
    )
    assert cargo_target, (
        "CARGO_TARGET_DIR must be set beneath the scratch root "
        "(${SCRATCH_DIR}/… or the staged ${TMP_RUST_PROJECT}/…)"
    )


def test_build_script_rustup_home_is_read_only_image_path(
    build_script_text: str,
) -> None:
    """RUSTUP_HOME points at the read-only image toolchain path, NOT scratch.

    Per spec FR-010 clarification (2026-06-15): the real cargo/rustc are
    invoked directly so RUSTUP_HOME stays on the read-only image and is never
    written. It must therefore NOT be rooted under the scratch dir.
    """
    rustup = re.search(r'RUSTUP_HOME="([^"]+)"', build_script_text)
    assert rustup, "RUSTUP_HOME must be set explicitly"
    value = rustup.group(1)
    assert "SCRATCH_DIR" not in value and "BUILD_SCRATCH_DIR" not in value, (
        "RUSTUP_HOME must NOT live under the scratch mount — it is the "
        f"read-only image toolchain home; got: {value!r}"
    )


def test_build_script_preflights_tools_with_naming_die(
    build_script_text: str,
) -> None:
    """Preflight resolves required tools via ``command -v`` and names the missing one."""
    # Each required tool is checked via command -v.
    for tool in ("cargo", "rustc", "cc", "curl", "oras"):
        assert tool in build_script_text, f"preflight must reference tool {tool!r}"
    assert "command -v" in build_script_text, (
        "tool preflight must use `command -v`"
    )
    # The failure path names the specific missing tool.
    assert re.search(
        r'die "required tool not found: \$\{?tool\}?"', build_script_text
    ), "missing-tool failure must die naming the specific tool"


def test_build_script_scratch_write_probe_names_path(
    build_script_text: str,
) -> None:
    """A scratch write-probe runs and its failure names the scratch path (FR-016)."""
    assert ".write-probe" in build_script_text, (
        "script must perform a scratch write-probe"
    )
    assert re.search(
        r'die "writable scratch mount is not writable: \$\{?SCRATCH_DIR\}?"',
        build_script_text,
    ), "write-probe failure must die naming the scratch path"


def test_build_script_emits_both_markers(build_script_text: str) -> None:
    """Both success markers are emitted in their stdout format (FR-013, FR-014)."""
    assert re.search(
        r'echo "BINARY_SHA256:\$\{?BINARY_SHA256\}?"', build_script_text
    ), "must emit the BINARY_SHA256: marker"
    assert re.search(
        r'echo "BINARY_OCI_REF:\$\{?OCI_REF\}?"', build_script_text
    ), "must emit the BINARY_OCI_REF: marker"


def test_build_script_markers_emitted_after_oras_push(
    build_script_text: str,
) -> None:
    """Markers print only after the oras push — never for an unpushed artifact (FR-016a/b)."""
    push_match = re.search(r"oras\s+push", build_script_text)
    assert push_match, "script must perform an oras push"

    sha_marker = build_script_text.index('echo "BINARY_SHA256:')
    ref_marker = build_script_text.index('echo "BINARY_OCI_REF:')

    assert push_match.start() < sha_marker, (
        "BINARY_SHA256 marker must appear AFTER the oras push in the file"
    )
    assert push_match.start() < ref_marker, (
        "BINARY_OCI_REF marker must appear AFTER the oras push in the file"
    )
