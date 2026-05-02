#!/usr/bin/env bash
# build-rust.sh — Build script executed by the Remote Executor inside the enclave.
#
# Installs the Rust toolchain, compiles the attested-hello binary, computes its
# SHA-256 digest, uploads it to GitHub Actions Artifacts via the v3 pipeline
# artifacts REST API, and prints stdout markers consumed by the workflow.
#
# Note: Uses the v3 API because the enclave environment only has curl available
# (the v4 API requires the @actions/artifact Node.js package). The v3 API is
# deprecated but remains functional and is the only option for non-runner
# environments.
#
# Required environment variables:
#   GITHUB_TOKEN            — GitHub token passed via the encrypted execution payload
#   GITHUB_RUN_ID           — The workflow run ID
#   GITHUB_REPOSITORY       — The repository slug (owner/repo)
#   ACTIONS_RUNTIME_TOKEN   — Runtime token for the Artifacts API
#   ACTIONS_RUNTIME_URL     — Base URL for the Actions runtime API
#
# Stdout markers emitted on success:
#   BINARY_SHA256:<64-char-hex>
#   BINARY_ARTIFACT_NAME:<name>

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINARY_NAME="attested-hello"
ARTIFACT_NAME="attested-hello-binary"
RUST_PROJECT_DIR="rust-project"
BINARY_REL_PATH="${RUST_PROJECT_DIR}/target/release/${BINARY_NAME}"

# ---------------------------------------------------------------------------
# Helper: print an error message to stderr and exit
# ---------------------------------------------------------------------------
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Step 1: Validate required environment variables
# ---------------------------------------------------------------------------
echo "=== Validating environment ===" >&2

: "${GITHUB_TOKEN:?GITHUB_TOKEN is not set — cannot upload artifact}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is not set — cannot upload artifact}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set — cannot upload artifact}"
: "${ACTIONS_RUNTIME_TOKEN:?ACTIONS_RUNTIME_TOKEN is not set — cannot upload artifact}"
: "${ACTIONS_RUNTIME_URL:?ACTIONS_RUNTIME_URL is not set — cannot upload artifact}"

echo "Environment validated." >&2

# ---------------------------------------------------------------------------
# Step 2: Install Rust toolchain via rustup (if not present)
# ---------------------------------------------------------------------------
echo "=== Installing Rust toolchain ===" >&2

if command -v cargo &>/dev/null; then
    echo "Rust toolchain already installed: $(rustc --version)" >&2
else
    echo "Installing Rust via rustup..." >&2
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable 2>&1 >&2 \
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
# Step 5: Upload binary to GitHub Actions Artifacts (v3 pipeline artifacts REST API)
#
# The v3 pipeline artifacts API flow:
#   1. POST to create an artifact → get fileContainerResourceUrl
#   2. PUT the file to the fileContainerResourceUrl
#   3. PATCH to finalize the artifact
# ---------------------------------------------------------------------------
echo "=== Uploading binary to GitHub Actions Artifacts ===" >&2

# Normalize the runtime URL (strip trailing slash)
RUNTIME_URL="${ACTIONS_RUNTIME_URL%/}"
ARTIFACTS_URL="${RUNTIME_URL}_apis/pipelines/workflows/${GITHUB_RUN_ID}/artifacts?api-version=6.0-preview"

# Step 5a: Create the artifact
echo "Creating artifact '${ARTIFACT_NAME}'..." >&2

CREATE_RESPONSE=$(curl -sS --fail-with-body \
    -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ACTIONS_RUNTIME_TOKEN}" \
    -d "{\"type\":\"actions_storage\",\"name\":\"${ARTIFACT_NAME}\"}" \
    "${ARTIFACTS_URL}" 2>&1) \
    || die "Failed to create artifact: ${CREATE_RESPONSE}"

# Extract the fileContainerResourceUrl from the response
FILE_CONTAINER_URL=$(echo "${CREATE_RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['fileContainerResourceUrl'])" 2>/dev/null) \
    || die "Failed to parse fileContainerResourceUrl from create response: ${CREATE_RESPONSE}"

echo "Artifact container created." >&2

# Step 5b: Upload the binary file
echo "Uploading binary..." >&2

UPLOAD_URL="${FILE_CONTAINER_URL}?itemPath=${ARTIFACT_NAME}/${BINARY_NAME}"

UPLOAD_RESPONSE=$(curl -sS --fail-with-body \
    -X PUT \
    -H "Content-Type: application/octet-stream" \
    -H "Authorization: Bearer ${ACTIONS_RUNTIME_TOKEN}" \
    -H "Content-Range: bytes 0-$(($(stat -c%s "${BINARY_REL_PATH}" 2>/dev/null || stat -f%z "${BINARY_REL_PATH}") - 1))/$(($(stat -c%s "${BINARY_REL_PATH}" 2>/dev/null || stat -f%z "${BINARY_REL_PATH}")))" \
    --data-binary "@${BINARY_REL_PATH}" \
    "${UPLOAD_URL}" 2>&1) \
    || die "Failed to upload binary to artifact: ${UPLOAD_RESPONSE}"

echo "Binary uploaded." >&2

# Step 5c: Finalize the artifact (PATCH to confirm upload is complete)
echo "Finalizing artifact..." >&2

FINALIZE_URL="${ARTIFACTS_URL}"
BINARY_SIZE=$(stat -c%s "${BINARY_REL_PATH}" 2>/dev/null || stat -f%z "${BINARY_REL_PATH}")

FINALIZE_RESPONSE=$(curl -sS --fail-with-body \
    -X PATCH \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ACTIONS_RUNTIME_TOKEN}" \
    -d "{\"size\":${BINARY_SIZE}}" \
    "${RUNTIME_URL}_apis/pipelines/workflows/${GITHUB_RUN_ID}/artifacts?artifactName=${ARTIFACT_NAME}&api-version=6.0-preview" 2>&1) \
    || die "Failed to finalize artifact upload: ${FINALIZE_RESPONSE}"

echo "Artifact '${ARTIFACT_NAME}' finalized." >&2

# ---------------------------------------------------------------------------
# Step 6: Print stdout markers for the workflow to parse
# ---------------------------------------------------------------------------
echo "BINARY_SHA256:${BINARY_SHA256}"
echo "BINARY_ARTIFACT_NAME:${ARTIFACT_NAME}"

echo "=== Build complete ===" >&2
