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
