#!/usr/bin/env bash
# build-rust.sh — Build script executed by the Remote Executor inside the
# hardened execution container.
#
# Runs under the executor's hardened defaults: rootless (uid:gid 65534:65534),
# read-only root filesystem, read-only /workspace, no-new-privileges. The Rust
# toolchain (cargo/rustc), the C compiler/linker (cc), curl, and the oras CLI
# are ALL pre-installed by the upstream rust-build flavor image at pinned versions
# (github-runner-ec2-attestation/flavors/rust-build/) — this script performs NO
# run-time package installs and NO toolchain download. It compiles
# the attested-hello binary, computes its SHA-256 digest, uploads it to GHCR as
# a temporary OCI package via oras, and prints stdout markers consumed by the
# workflow.
#
# The binary is transferred from the enclave to the workflow via a temporary
# GHCR package. The workflow pulls it back using `oras pull`, verifies the
# digest, and then pushes the final attested artifact. A cleanup step in the
# workflow deletes the temporary package after completion.
#
# Filesystem model (hardened defaults):
#   * Root filesystem and /workspace are READ-ONLY — nothing is written there.
#   * A single writable tmpfs scratch mount is the ONLY writable location. Its
#     root is resolved from BUILD_SCRATCH_DIR (default /tmp); every write target
#     (cargo home, cargo target dir, the source copy, oras auth) is a
#     subdirectory beneath it.
#   * RUSTUP_HOME points at the read-only toolchain home in the image. It is
#     SET but never written: the real cargo/rustc binaries (invoked directly,
#     not via the rustup proxy shims) do not write under RUSTUP_HOME.
#
# Required environment variables:
#   GITHUB_TOKEN       — GitHub token for GHCR authentication (passed via script_env)
#   GITHUB_REPOSITORY  — Repository slug, e.g. owner/repo (passed via script_env)
#   COMMIT_SHA         — The commit SHA being built (passed via script_env)
#   BUILD_SCRATCH_DIR  — Optional. Writable scratch root; defaults to /tmp.
#
# Stdout markers emitted ONLY on full success (after a successful oras push):
#   BINARY_SHA256:<64-char-hex>
#   BINARY_OCI_REF:<oci-reference>

set -euo pipefail

# ---------------------------------------------------------------------------
# Helper: print an error message to stderr and exit non-zero
# ---------------------------------------------------------------------------
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Scratch root + write targets (the single writable tmpfs mount)
# ---------------------------------------------------------------------------
# The executor mounts the writable scratch tmpfs at /tmp; the env override
# keeps the script portable if that mount path ever changes (research R5).
SCRATCH_DIR="${BUILD_SCRATCH_DIR:-/tmp}"

BINARY_NAME="attested-hello"
WORKSPACE_RUST_PROJECT="/workspace/rust-project"
TMP_RUST_PROJECT="${SCRATCH_DIR}/rust-project"
ORAS_AUTH="${SCRATCH_DIR}/oras-auth.json"

# Writable cargo state lives under scratch; the toolchain home stays read-only.
export CARGO_HOME="${SCRATCH_DIR}/.cargo"
export CARGO_TARGET_DIR="${SCRATCH_DIR}/target"

BINARY_PATH="${CARGO_TARGET_DIR}/release/${BINARY_NAME}"

# Toolchain contract: these defaults are the contract provided by the upstream
# rust-build flavor (github-runner-ec2-attestation/flavors/rust-build/).
# The flavor's image pre-installs cargo, rustc, cc, curl, and oras on the runtime
# PATH for 65534:65534, with RUSTUP_HOME=/opt/rust (read-only, never written —
# real binaries are invoked directly, not via rustup proxy shims) and the toolchain
# binaries at /opt/rust/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin.
# A divergence in the upstream flavor from these values breaks this script.
export RUSTUP_HOME="${RUSTUP_HOME:-/opt/rust}"

# The real toolchain bin dir and oras install dir are baked into the upstream
# rust-build flavor image; defaults below must stay byte-identical to that flavor.
RUST_TOOLCHAIN_BIN="${RUST_TOOLCHAIN_BIN:-/opt/rust/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin}"
export PATH="${RUST_TOOLCHAIN_BIN}:/usr/local/bin:${PATH}"

# ---------------------------------------------------------------------------
# Step 1: Preflight — required env vars, required tools, writable scratch
# ---------------------------------------------------------------------------
echo "=== Preflight ===" >&2

# 1a. Required environment variables (missing → non-zero exit naming the var).
: "${GITHUB_TOKEN:?GITHUB_TOKEN is not set — cannot authenticate to GHCR}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set — cannot determine GHCR package path}"
: "${COMMIT_SHA:?COMMIT_SHA is not set — cannot generate unique tag}"

# 1b. Required tools must be pre-installed in the image and on PATH. A missing
# tool is named explicitly; the script never installs or escalates (FR-016).
for tool in cargo rustc cc curl oras; do
    command -v "${tool}" >/dev/null 2>&1 \
        || die "required tool not found: ${tool}"
done

# 1c. The scratch mount must be writable. This is the ONLY proactive
# filesystem check; every other write target lives beneath ${SCRATCH_DIR} and
# is enforced read-only at the OS level if it strays (FR-016 / research R6).
if ! : > "${SCRATCH_DIR}/.write-probe" 2>/dev/null; then
    die "writable scratch mount is not writable: ${SCRATCH_DIR}"
fi
rm -f "${SCRATCH_DIR}/.write-probe"

mkdir -p "${CARGO_HOME}" \
    || die "failed to create CARGO_HOME under the writable scratch mount: ${CARGO_HOME}"

echo "Preflight passed (SCRATCH_DIR=${SCRATCH_DIR})." >&2
echo "RUSTUP_HOME=${RUSTUP_HOME} (read-only)" >&2
echo "CARGO_HOME=${CARGO_HOME}" >&2
echo "CARGO_TARGET_DIR=${CARGO_TARGET_DIR}" >&2

# ---------------------------------------------------------------------------
# Step 2: Stage the Rust project source into scratch (/workspace is read-only)
# ---------------------------------------------------------------------------
echo "=== Staging Rust project into scratch ===" >&2

if [ ! -d "${WORKSPACE_RUST_PROJECT}" ]; then
    die "Rust project directory '${WORKSPACE_RUST_PROJECT}' not found"
fi

rm -rf "${TMP_RUST_PROJECT}"
mkdir -p "${TMP_RUST_PROJECT}"
find "${WORKSPACE_RUST_PROJECT}" -mindepth 1 -maxdepth 1 ! -name 'target' -exec cp -r {} "${TMP_RUST_PROJECT}/" \; \
    || die "failed to copy Rust project from ${WORKSPACE_RUST_PROJECT} into scratch at ${TMP_RUST_PROJECT}"

echo "Rust project staged at ${TMP_RUST_PROJECT}" >&2

# ---------------------------------------------------------------------------
# Step 3: Build the Rust project in release mode (writes only under scratch)
# ---------------------------------------------------------------------------
echo "=== Building Rust project (cargo build --release) ===" >&2

(cd "${TMP_RUST_PROJECT}" && cargo build --release >&2) \
    || die "cargo build --release failed — the compile/link step exited non-zero (if disk-related, the writable scratch mount may be exhausted)"

if [ ! -f "${BINARY_PATH}" ]; then
    die "expected binary not found after build at ${BINARY_PATH}"
fi

echo "Build succeeded: ${BINARY_PATH}" >&2

# ---------------------------------------------------------------------------
# Step 4: Compute SHA-256 digest over the complete binary
# ---------------------------------------------------------------------------
echo "=== Computing SHA-256 digest ===" >&2

BINARY_SHA256=$(sha256sum "${BINARY_PATH}" | awk '{print $1}')

if [ -z "${BINARY_SHA256}" ] || [ "${#BINARY_SHA256}" -ne 64 ]; then
    die "failed to compute a valid SHA-256 digest for ${BINARY_PATH}"
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
# Step 6: Authenticate to GHCR via oras (pre-installed in the image)
# ---------------------------------------------------------------------------
echo "=== Authenticating to GHCR ===" >&2

echo "${GITHUB_TOKEN}" | oras login ghcr.io \
    --username github \
    --password-stdin \
    --registry-config "${ORAS_AUTH}" \
    || die "failed to authenticate to GHCR via oras login (push/egress failure)"

echo "GHCR authentication succeeded." >&2

# ---------------------------------------------------------------------------
# Step 7: Push the binary to GHCR as a temporary OCI package
# ---------------------------------------------------------------------------
echo "=== Pushing binary to GHCR ===" >&2

OCI_REF="ghcr.io/${GITHUB_REPOSITORY}/tmp-build:${TAG}"

# Push from the directory containing the binary so the layer name is just the filename
(cd "$(dirname "${BINARY_PATH}")" && \
    oras push \
        --registry-config "${ORAS_AUTH}" \
        "${OCI_REF}" \
        --annotation "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY}" \
        "${BINARY_NAME}:application/octet-stream") \
    || die "failed to push binary to GHCR at ${OCI_REF} (push/egress failure)"

echo "Binary pushed to ${OCI_REF}" >&2

# ---------------------------------------------------------------------------
# Step 8: Print stdout markers — ONLY after a successful push (no partial
# artifact is ever advertised; FR-016a/FR-016b)
# ---------------------------------------------------------------------------
echo "BINARY_SHA256:${BINARY_SHA256}"
echo "BINARY_OCI_REF:${OCI_REF}"

echo "=== Build complete ===" >&2
