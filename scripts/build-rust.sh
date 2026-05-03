#!/usr/bin/env bash
# build-rust.sh — Build script executed by the Remote Executor inside the enclave.
#
# Installs the Rust toolchain, compiles the attested-hello binary, computes its
# SHA-256 digest, uploads it to GHCR as a temporary OCI package via Oras, and
# prints stdout markers consumed by the workflow.
#
# The binary is transferred from the enclave to the workflow via a temporary
# GHCR package. The workflow pulls it back using `oras pull`, verifies the
# digest, and then pushes the final attested artifact. A cleanup step in the
# workflow deletes the temporary package after completion.
#
# Required environment variables:
#   GITHUB_TOKEN      — GitHub token for GHCR authentication (passed via script_env)
#   GITHUB_REPOSITORY — Repository slug, e.g. owner/repo (passed via script_env)
#   COMMIT_SHA        — The commit SHA being built (passed via script_env)
#
# Stdout markers emitted on success:
#   BINARY_SHA256:<64-char-hex>
#   BINARY_OCI_REF:<oci-reference>

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINARY_NAME="attested-hello"
RUST_PROJECT_DIR="rust-project"
BINARY_REL_PATH="${RUST_PROJECT_DIR}/target/release/${BINARY_NAME}"
ORAS_VERSION="1.3.2"
ORAS_TARBALL="/tmp/oras_${ORAS_VERSION}_linux_amd64.tar.gz"
ORAS_BIN="/tmp/oras"
ORAS_AUTH="/tmp/oras-auth.json"

# ---------------------------------------------------------------------------
# Helper: print an error message to stderr and exit
# ---------------------------------------------------------------------------
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Helper: portable HTTP download (prefers curl, falls back to wget)
# ---------------------------------------------------------------------------
download() {
    local url="$1"
    local dest="${2:-}"  # empty means stdout

    if command -v curl &>/dev/null; then
        if [ -n "$dest" ]; then
            curl -sSL -o "$dest" "$url"
        else
            curl -sSfL "$url"
        fi
    elif command -v wget &>/dev/null; then
        if [ -n "$dest" ]; then
            wget -qO "$dest" "$url"
        else
            wget -qO- "$url"
        fi
    else
        die "Neither curl nor wget is available — cannot download ${url}"
    fi
}

# ---------------------------------------------------------------------------
# Step 1: Validate required environment variables
# ---------------------------------------------------------------------------
echo "=== Validating environment ===" >&2

: "${GITHUB_TOKEN:?GITHUB_TOKEN is not set — cannot authenticate to GHCR}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set — cannot determine GHCR package path}"
: "${COMMIT_SHA:?COMMIT_SHA is not set — cannot generate unique tag}"

echo "Environment validated." >&2

# ---------------------------------------------------------------------------
# Step 2: Install Rust toolchain via rustup (if not present)
# ---------------------------------------------------------------------------
echo "=== Installing Rust toolchain ===" >&2

if command -v cargo &>/dev/null; then
    echo "Rust toolchain already installed: $(rustc --version)" >&2
else
    echo "Installing Rust via rustup..." >&2
    download "https://sh.rustup.rs" | sh -s -- -y --default-toolchain stable 2>&1 >&2 \
        || die "Failed to install Rust toolchain via rustup"
    # shellcheck source=/dev/null
    source "${HOME}/.cargo/env"
    echo "Rust installed: $(rustc --version)" >&2
fi

# ---------------------------------------------------------------------------
# Step 3: Build the Rust project in release mode
# ---------------------------------------------------------------------------
echo "=== Building Rust project ===" >&2

if [ ! -d "${RUST_PROJECT_DIR}" ]; then
    die "Rust project directory '${RUST_PROJECT_DIR}' not found in $(pwd)"
fi

(cd "${RUST_PROJECT_DIR}" && cargo build --release 2>&1 >&2) \
    || die "cargo build --release failed"

if [ ! -f "${BINARY_REL_PATH}" ]; then
    die "Expected binary not found at ${BINARY_REL_PATH}"
fi

echo "Build succeeded: ${BINARY_REL_PATH}" >&2

# ---------------------------------------------------------------------------
# Step 4: Compute SHA-256 digest of the binary
# ---------------------------------------------------------------------------
echo "=== Computing SHA-256 digest ===" >&2

BINARY_SHA256=$(sha256sum "${BINARY_REL_PATH}" | awk '{print $1}')

if [ -z "${BINARY_SHA256}" ] || [ "${#BINARY_SHA256}" -ne 64 ]; then
    die "Failed to compute valid SHA-256 digest"
fi

echo "SHA-256: ${BINARY_SHA256}" >&2

# ---------------------------------------------------------------------------
# Step 5: Generate a unique tag for the temporary GHCR package
# ---------------------------------------------------------------------------
echo "=== Generating unique tag ===" >&2

SHORT_SHA="${COMMIT_SHA:0:7}"
RANDOM_SUFFIX=$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 6)
TAG="${SHORT_SHA}-${RANDOM_SUFFIX}"

echo "Tag: ${TAG}" >&2

# ---------------------------------------------------------------------------
# Step 6: Install Oras CLI v1.3.2 (entirely within /tmp/)
# ---------------------------------------------------------------------------
echo "=== Installing Oras CLI v${ORAS_VERSION} ===" >&2

if [ -x "${ORAS_BIN}" ]; then
    echo "Oras CLI already installed at ${ORAS_BIN}" >&2
else
    download \
        "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz" \
        "${ORAS_TARBALL}" \
        || die "Failed to download Oras CLI v${ORAS_VERSION}"

    tar -zxf "${ORAS_TARBALL}" -C /tmp oras \
        || die "Failed to extract Oras CLI from tarball"

    rm -f "${ORAS_TARBALL}"

    chmod +x "${ORAS_BIN}"
    echo "Oras CLI installed at ${ORAS_BIN}" >&2
fi

# ---------------------------------------------------------------------------
# Step 7: Authenticate to GHCR via Oras
# ---------------------------------------------------------------------------
echo "=== Authenticating to GHCR ===" >&2

echo "${GITHUB_TOKEN}" | "${ORAS_BIN}" login ghcr.io \
    --username github \
    --password-stdin \
    --registry-config "${ORAS_AUTH}" \
    || die "Failed to authenticate to GHCR via oras login"

echo "GHCR authentication succeeded." >&2

# ---------------------------------------------------------------------------
# Step 8: Push binary to GHCR as a temporary OCI package
# ---------------------------------------------------------------------------
echo "=== Pushing binary to GHCR ===" >&2

OCI_REF="ghcr.io/${GITHUB_REPOSITORY}/tmp-build:${TAG}"

# Push from the directory containing the binary so the layer name is just the filename
(cd "$(dirname "${BINARY_REL_PATH}")" && \
    "${ORAS_BIN}" push \
        --registry-config "${ORAS_AUTH}" \
        "${OCI_REF}" \
        --annotation "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY}" \
        "${BINARY_NAME}:application/octet-stream") \
    || die "Failed to push binary to GHCR at ${OCI_REF}"

echo "Binary pushed to ${OCI_REF}" >&2

# ---------------------------------------------------------------------------
# Step 9: Print stdout markers for the workflow to parse
# ---------------------------------------------------------------------------
echo "BINARY_SHA256:${BINARY_SHA256}"
echo "BINARY_OCI_REF:${OCI_REF}"

echo "=== Build complete ===" >&2
