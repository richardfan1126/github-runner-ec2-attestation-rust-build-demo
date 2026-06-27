# Attested Rust Build Pipeline

Builds a Rust binary inside an attested AWS Nitro Enclave environment, verifies the binary's integrity, bundles it with attestation documents, pushes the result to GHCR as an OCI artifact via Oras, and creates a GitHub Artifact Attestation for supply-chain provenance.

## Overview

This project demonstrates an end-to-end attested build pipeline:

1. A GitHub Actions workflow dispatches a build request to a Remote Executor running in an AWS Nitro Enclave
2. The Remote Executor compiles a Rust binary (`attested-hello`) inside the enclave using a pre-installed, digest-pinned toolchain — no run-time installs
3. The binary is pushed to a temporary GHCR package from within the enclave using the pre-installed oras CLI
4. The workflow pulls the binary from the temporary GHCR package and verifies its SHA-256 digest against the value reported by the build script
5. The binary, attestation documents, and a provenance manifest are packaged and pushed to GHCR as an OCI artifact via Oras
6. A Sigstore-based GitHub Artifact Attestation is created for supply-chain provenance

## Build-Sign-Upload Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ GitHub Actions Runner                                           │
│                                                                 │
│  1. Validate inputs                                             │
│  2. Checkout repository                                         │
│  3. Install Python + caller dependencies + oras CLI             │
│  4. Invoke caller module ──► Remote Executor (Nitro Enclave)    │
│     │                         ├─ cargo build --release          │
│     │                         ├─ Compute SHA-256 of binary      │
│     │                         └─ Push binary to GHCR tmp pkg    │
│     ◄── stdout markers + attestation documents ─────────────┘   │
│  5. Parse BINARY_SHA256 and BINARY_OCI_REF from stdout          │
│  6. Pull binary from GHCR tmp package (oras pull)               │
│  7. Verify SHA-256 digest matches                               │
│  8. Create provenance manifest                                  │
│  9. Package attestation bundle                                  │
│ 10. oras push (binary + attestation bundle + provenance) → GHCR │
│ 11. gh attestation create (Sigstore) → GHCR                     │
│ 12. Cleanup temporary GHCR package                              │
│ 13. Upload attestation-documents as Actions artifact             │
└─────────────────────────────────────────────────────────────────┘
```

## Configuring the Hardened Build Image (Operator Setup)

### Build environment: upstream rust-build flavor

The execution container image is **not** published by this repository. It is provided by
the upstream `rust-build` flavor (`github-runner-ec2-attestation/flavors/rust-build/`),
which is baked offline into a per-flavor, PCR4-bound attestable AMI by the upstream pipeline —
nothing is pulled from a registry at executor runtime.

To run this build, configure the executor to use the AMI produced for the `rust-build` flavor.
The upstream pipeline records the flavor image digest in `flavors.lock` and bakes it into the
AMI's dm-verity-sealed root at build time; the PCR4 value in the AMI binds the executor to
that exact image digest. Refer to the `github-runner-ec2-attestation` repository for flavor
selection and AMI provisioning instructions.

> **Model change:** The previous model required operators to pin a
> `build-image@sha256:<digest>` from this repository's GHCR into the executor's container
> config. That model no longer applies — select the `rust-build` flavor AMI from the upstream
> pipeline instead.

### Writable scratch mount (provided by the flavor)

The `rust-build` flavor provisions the executor's single writable tmpfs scratch mount at
**2 GiB** with exec enabled (`CONTAINER_TMPFS_SIZE=2g`, `CONTAINER_TMPFS_EXEC=true` in
`flavors/rust-build/env`). Operators do not size this themselves — it is baked into the
PCR4-bound AMI by the upstream pipeline. The 2 GiB tmpfs covers:

- Rust toolchain writable home/caches (`CARGO_HOME` — index, lock, metadata)
- Release build artifacts (`CARGO_TARGET_DIR/release/` — compiled objects + final binary)
- Source copy staged to scratch (`rust-project/`)
- oras authentication and push scratch
- Headroom for filesystem overhead

Exec must be enabled on the scratch tmpfs because the release build runs compiled output
(build scripts and the final `attested-hello` binary) from `CARGO_TARGET_DIR`. Measured peak
scratch for `attested-hello` (zero crate dependencies) is well under 2 GiB.

### No operator security changes required

Operators change **nothing** in the executor's security configuration: selecting the `rust-build`
flavor AMI is the whole setup. Compatibility comes from the upstream `rust-build` flavor and this
repo's build script. The flavor carries two deliberate, build-required relaxations from the hardened
executor defaults — both baked into the PCR4-bound AMI and recorded in the upstream `flavors.lock`
`relaxations` field, so they are visible to a verifier without reading the env file:

| Setting | Hardened executor default | rust-build flavor |
|---|---|---|
| User | `65534:65534` | unchanged |
| Root filesystem | read-only | unchanged |
| Workspace mount | read-only | unchanged |
| Linux capabilities | default set (no extras) | unchanged |
| `no-new-privileges` | enabled | unchanged |
| Writable tmpfs scratch | `256m`, `noexec` | `2g`, `exec` — **relaxed** (compile + run from scratch) |
| Network mode | `none` (no egress) | `bridge` — **relaxed** (outbound egress for the final `oras push` to GHCR) |

No other security setting deviates from its hardened default, and the operator makes none of these
changes by hand — they ship inside the flavor's attestable AMI.

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
