# Attested Rust Build Pipeline

Builds a Rust binary inside an attested AWS Nitro Enclave environment, verifies the binary's integrity, bundles it with attestation documents, pushes the result to GHCR as an OCI artifact via Oras, and creates a GitHub Artifact Attestation for supply-chain provenance.

## Overview

This project demonstrates an end-to-end attested build pipeline:

1. A GitHub Actions workflow dispatches a build request to a Remote Executor running in an AWS Nitro Enclave
2. The Remote Executor compiles a Rust binary (`attested-hello`) inside the enclave
3. The binary is uploaded to GitHub Actions Artifacts from within the enclave using the GitHub Actions Artifacts API
4. The workflow downloads the binary, verifies its SHA-256 digest against the value reported by the build script
5. The binary, attestation documents, and a provenance manifest are packaged and pushed to GHCR as an OCI artifact via Oras
6. A Sigstore-based GitHub Artifact Attestation is created for supply-chain provenance

## Build-Sign-Upload Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ GitHub Actions Runner                                           │
│                                                                 │
│  1. Validate inputs                                             │
│  2. Checkout repository                                         │
│  3. Install Python + caller dependencies                        │
│  4. Invoke caller module ──► Remote Executor (Nitro Enclave)    │
│     │                         ├─ Install Rust toolchain         │
│     │                         ├─ cargo build --release          │
│     │                         ├─ Compute SHA-256 of binary      │
│     │                         └─ Upload binary to GH Artifacts  │
│     ◄── stdout markers + attestation documents ─────────────┘   │
│  5. Parse BINARY_SHA256 and BINARY_ARTIFACT_NAME from stdout    │
│  6. Download binary artifact                                    │
│  7. Verify SHA-256 digest matches                               │
│  8. Create provenance manifest                                  │
│  9. Package attestation bundle                                  │
│ 10. oras push (binary + attestation bundle + provenance) → GHCR │
│ 11. gh attestation create (Sigstore) → GHCR                     │
│ 12. Upload attestation-documents as Actions artifact             │
└─────────────────────────────────────────────────────────────────┘
```

## Configuring the Hardened Build Image (Operator Setup)

### Build-image reference

Point the executor at the **immutable-digest** reference published by the `Build Hardened Build Image`
workflow — never a floating tag:

```
ghcr.io/<owner>/github-runner-ec2-attestation-rust-build-demo/build-image@sha256:<digest>
```

Obtain the current digest from the **Build Hardened Build Image** workflow's job summary after it
completes. The summary prints the full immutable reference in the form:

```
ghcr.io/<owner>/github-runner-ec2-attestation-rust-build-demo/build-image@sha256:abc123…
```

Re-pin whenever `Dockerfile` or `scripts/build-rust.sh` changes — each push to those paths triggers a
new workflow run that produces a new digest.

### Minimum writable scratch-mount size

Configure the executor's writable tmpfs scratch mount to **≥ 4 GiB**. This floor covers:

- Rust toolchain writable home/caches (`CARGO_HOME` — index, lock, metadata)
- Release build artifacts (`CARGO_TARGET_DIR/release/` — compiled objects + final binary)
- Source copy staged to scratch (`rust-project/`)
- oras authentication and push scratch
- Headroom for filesystem overhead

Actual peak scratch for `attested-hello` (zero crate dependencies) is expected to be well under 4 GiB;
this floor stays conservative until measured. To validate or lower it, run `quickstart.md` Scenario D,
which reports `PEAK_SCRATCH_MB` for a real release build.

### No executor security changes required

Change **nothing** in the executor's security configuration. The image and build script run under the
executor's hardened defaults without modification:

| Setting | Executor default | Required change |
|---|---|---|
| User | `65534:65534` | None |
| Root filesystem | Read-only | None |
| Workspace mount | Read-only | None |
| Linux capabilities | Default set (no extras) | None |
| `no-new-privileges` | Enabled | None |
| Network egress | Permitted | None (required for GHCR push) |

The only pre-existing executor permission this build relies on is **outbound network egress** for the
final `oras push` to GHCR. All other security settings remain at their hardened defaults.

---

## Triggering the Workflow

The workflow is triggered via `workflow_dispatch` from the GitHub Actions UI or the GitHub CLI.

### Workflow Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `server_url` | Yes | — | Base URL of the Remote Executor server |
| `script_path` | No | `scripts/build-rust.sh` | Path to the build script in the repository |
| `commit_hash` | No | Current SHA | Git commit SHA to build |
| `repository_url` | No | Current repository | Git repository URL |
| `audience` | No | — | OIDC audience value |
| `server_url_allowlist` | No | — | Comma-separated list of allowed server URLs |

### Trigger via GitHub CLI

```bash
gh workflow run attested-rust-build.yml \
  -f server_url="http://203.0.113.42:8080"
```

### Trigger with All Options

```bash
gh workflow run attested-rust-build.yml \
  -f server_url="http://203.0.113.42:8080" \
  -f script_path="scripts/build-rust.sh" \
  -f commit_hash="abc1234" \
  -f server_url_allowlist="http://203.0.113.42:8080,http://198.51.100.10:8080"
```

## Verifying the OCI Artifact

After the workflow completes, the attested binary is available as an OCI artifact on GHCR.

### Pull the Artifact with Oras

```bash
# Pull all layers (binary + attestation bundle + provenance manifest)
oras pull ghcr.io/<owner>/github-runner-ec2-attestation-rust-build-demo/attested-hello:<short-sha>
```

This downloads:
- `attested-hello` — the compiled Rust binary
- `attestation-bundle.tar.gz` — the Nitro attestation documents
- `provenance.json` — the provenance manifest linking the binary to its build metadata

### Verify with GitHub Attestation

```bash
# Verify the Sigstore-based GitHub Artifact Attestation
gh attestation verify oci://ghcr.io/<owner>/github-runner-ec2-attestation-rust-build-demo/attested-hello:<short-sha>
```

This checks that the OCI artifact was produced by a specific GitHub Actions workflow run and has not been tampered with.

## Development

### Prerequisites

- Python 3.11+
- Rust toolchain (for local builds only; the Remote Executor installs its own)

### Install Dependencies

```bash
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

## License

See LICENSE file for details.
