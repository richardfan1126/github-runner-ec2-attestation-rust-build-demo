# Requirements Document

## Introduction

This feature delivers a GitHub Actions workflow and supporting scripts that build a Rust binary on a remote attested executor (using the `github-runner-ec2-attestation` system), sign the built binary with the attestation document retrieved from the remote executor, and upload the signed binary as an OCI artifact to GitHub Container Registry (GHCR) using the Oras CLI. The project is a standalone demo that reuses the caller communication pattern from `github-runner-ec2-attestation-caller`.

## Glossary

- **Workflow**: A GitHub Actions workflow definition (YAML) that orchestrates the end-to-end build, sign, and upload pipeline.
- **Caller**: The Python-based client (`call_remote_executor` module) that communicates with the Remote Executor server via the attested channel (health check, OIDC, attestation, PQ_Hybrid_KEM, encrypted execution, polling).
- **Remote_Executor**: The server-side component running on an AWS Nitro Enclave that executes scripts in an attested environment and returns output with attestation documents. The Remote_Executor mounts the cloned project repository read-only at `/workspace` inside the Execution_Container.
- **Build_Script**: A shell script committed to the repository that the Remote_Executor clones and runs to compile the Rust binary. The Build_Script executes with `/workspace` as its working directory, where the project repository is mounted read-only. The Build_Script must copy source files to `/tmp/` for compilation since `/workspace` is not writable.
- **Execution_Container**: The Docker container inside the Remote_Executor's enclave environment where the Build_Script runs. The project repository is mounted read-only at `/workspace`, making the Rust project source available at `/workspace/rust-project/`. Only `/tmp/` is writable inside the Execution_Container.
- **Attestation_Document**: A COSE Sign1 document produced by the AWS Nitro Enclave that cryptographically proves the execution environment identity and integrity. For execution-acceptance attestations, the `user_data` field contains a JSON object with `repository_url`, `commit_hash`, `script_path`, `script_env_hash`, and `timestamp`.
- **script_env_hash**: A SHA-256 hex digest of the canonicalized `script_env` dictionary included in the execution-acceptance attestation's `user_data`. Canonicalization: sort keys lexicographically, serialize as JSON with compact separators (`(',', ':')`), no whitespace. When `script_env` is empty or not provided, the hash is computed over `{}` (empty JSON object). This field enables consumers to verify that no unexpected environment variables were injected.
- **Binary_Artifact**: The compiled Rust executable produced by the Build_Script on the Remote_Executor.
- **Signing_Script**: A script that attaches the Attestation_Document to the Binary_Artifact as a cryptographic provenance record.
- **GHCR**: GitHub Container Registry, an OCI-compliant registry at `ghcr.io`.
- **Oras**: The OCI Registry As Storage CLI tool used to push and pull arbitrary artifacts to OCI registries.
- **OCI_Artifact**: An artifact stored in an OCI registry, consisting of the Binary_Artifact and its associated Attestation_Document as layers or annotations.
- **Rust_Project**: A minimal Rust project (Cargo.toml and source files) that compiles into the Binary_Artifact.
- **Attestation_Bundle**: A directory containing the server-identity, execution-acceptance, and output-integrity attestation documents and their manifest, produced by the Caller during execution.
- **Temporary_GHCR_Package**: A temporary OCI artifact pushed to GHCR by the Build_Script from inside the enclave, used to transfer the Binary_Artifact to the GitHub Actions runner. The Build_Script installs the Oras CLI, authenticates to GHCR using the `GITHUB_TOKEN` (received in the encrypted execution payload), and pushes the binary via `oras push`. The Workflow pulls the binary using `oras pull`, and a cleanup step deletes the temporary package via the GitHub Packages REST API after the workflow completes (on both success and failure).
- **GitHub_Attestation**: A Sigstore-based attestation created by the `actions/attest@v4` GitHub Action that binds a build artifact to the GitHub Actions workflow run, providing supply-chain provenance via GitHub's artifact attestation feature. Verified by consumers using `gh attestation verify`.

## Requirements

### Requirement 1: Rust Project Structure

**User Story:** As a developer, I want a minimal Rust project in the repository, so that the Remote_Executor can compile it into a Binary_Artifact.

#### Acceptance Criteria

1. THE Rust_Project SHALL contain a valid `Cargo.toml` with a binary target named `attested-hello`.
2. THE Rust_Project SHALL contain a `src/main.rs` that compiles into a statically-linked executable.
3. WHEN compiled with `cargo build --release`, THE Rust_Project SHALL produce a single Binary_Artifact at `target/release/attested-hello`.
4. THE Binary_Artifact SHALL print a version string and a build timestamp to stdout when executed.

### Requirement 2: Remote Build Script

**User Story:** As a developer, I want a build script that compiles the Rust project on the Remote_Executor and uploads the binary to GHCR as a temporary package, so that the binary is built inside the attested environment and can be retrieved by the workflow.

#### Acceptance Criteria

1. THE Build_Script SHALL be a shell script located at `scripts/build-rust.sh` in the repository.
2. WHEN executed by the Remote_Executor, THE Build_Script SHALL have `/workspace` as its working directory, where the project repository is mounted read-only inside the Execution_Container.
3. SINCE `/workspace` is read-only, THE Build_Script SHALL copy the Rust project source from `/workspace/rust-project/` to a writable location under `/tmp/` before compiling.
4. WHEN executed by the Remote_Executor, THE Build_Script SHALL install the Rust toolchain if not already present.
5. WHEN executed by the Remote_Executor, THE Build_Script SHALL run `cargo build --release` in the copied Rust project directory under `/tmp/`.
6. WHEN the build succeeds, THE Build_Script SHALL compute a SHA-256 digest of the Binary_Artifact and print it to stdout in the format `BINARY_SHA256:<hex_digest>`.
7. WHEN the build succeeds, THE Build_Script SHALL install the Oras CLI (if not already present), authenticate to GHCR using `GITHUB_TOKEN` via `oras login`, and upload the Binary_Artifact to GHCR as a Temporary_GHCR_Package via `oras push`, printing the full OCI reference to stdout in the format `BINARY_OCI_REF:<reference>`.
8. THE Build_Script SHALL push the Temporary_GHCR_Package to `ghcr.io/<GITHUB_REPOSITORY>/tmp-build/<tag>` where `<tag>` is derived from the commit SHA and a unique suffix to avoid collisions. THE push SHALL include the `org.opencontainers.image.source` annotation pointing to the repository URL to link the package to the repository for cleanup permissions.
9. IF the Rust toolchain installation fails, THEN THE Build_Script SHALL exit with a non-zero exit code and print a descriptive error to stderr.
10. IF `cargo build --release` fails, THEN THE Build_Script SHALL exit with a non-zero exit code and print the compiler error output to stderr.
11. IF the upload to GHCR fails, THEN THE Build_Script SHALL exit with a non-zero exit code and print a descriptive error to stderr.

### Requirement 3: GitHub Actions Workflow — Attested Build and Upload

**User Story:** As a developer, I want a GitHub Actions workflow that orchestrates the attested build, signing, and GHCR upload, so that I can produce verifiably attested Rust binaries with a single workflow dispatch.

#### Acceptance Criteria

1. THE Workflow SHALL be triggered by `workflow_dispatch` with inputs for `server_url` (required), `script_path` (optional, default `scripts/build-rust.sh`), `commit_hash` (optional, default current SHA), `repository_url` (optional, default current repository), `audience` (optional), and `server_url_allowlist` (optional).
2. THE Workflow SHALL request `id-token: write`, `contents: read`, `packages: write`, and `attestations: write` permissions.
3. THE Workflow SHALL validate that `server_url` is non-empty before proceeding.
4. WHEN `server_url_allowlist` is provided, THE Workflow SHALL reject any `server_url` not present in the comma-separated allowlist.
5. THE Workflow SHALL check out the repository at the specified `commit_hash` or the current SHA.
6. THE Workflow SHALL install Python 3.11 and the Caller dependencies from `pyproject.toml`.
7. THE Workflow SHALL invoke the Caller with the configured inputs, `--root-cert-pem`, `--expected-pcrs`, and `--attestation-output-dir attestation-documents`.
8. WHEN the Caller completes with exit code 0, THE Workflow SHALL proceed to the signing and upload steps.
9. IF the Caller exits with a non-zero exit code, THEN THE Workflow SHALL fail the job and upload any available attestation documents as artifacts.

### Requirement 4: Binary Retrieval from GHCR and Verification in Workflow

**User Story:** As a developer, I want the workflow to download the compiled binary from the temporary GHCR package pushed by the build script, verify its integrity, and then use it for signing and final publishing.

#### Acceptance Criteria

1. WHEN the build succeeds, THE Build_Script SHALL upload the Binary_Artifact to GHCR as a Temporary_GHCR_Package using the `GITHUB_TOKEN` received in the encrypted execution payload.
2. THE Build_Script SHALL print the OCI reference to stdout in the format `BINARY_OCI_REF:<reference>`.
3. THE Build_Script SHALL print the SHA-256 digest of the Binary_Artifact to stdout in the format `BINARY_SHA256:<hex_digest>`.
4. WHEN the Caller completes successfully, THE Workflow SHALL parse the execution stdout to extract the `BINARY_OCI_REF` and `BINARY_SHA256` values.
5. THE Workflow SHALL pull the Binary_Artifact from GHCR using Oras with the extracted OCI reference.
6. THE Workflow SHALL compute a SHA-256 digest of the downloaded Binary_Artifact and verify it matches the `BINARY_SHA256` value from the build output.
7. IF the SHA-256 digest of the downloaded Binary_Artifact does not match the `BINARY_SHA256` value, THEN THE Workflow SHALL fail with a descriptive integrity mismatch error.
8. IF the `BINARY_OCI_REF` or `BINARY_SHA256` markers are missing from the execution stdout, THEN THE Workflow SHALL fail with a descriptive error message.

### Requirement 5: Attestation-Based Signing

**User Story:** As a developer, I want the built binary to be signed with the attestation document, so that consumers can verify the binary was built in a trusted environment.

#### Acceptance Criteria

1. THE Signing_Script SHALL create an Attestation_Bundle directory containing the server-identity, execution-acceptance, and output-integrity attestation documents.
2. THE Signing_Script SHALL produce a JSON provenance manifest that includes the binary digest, the attestation document references, the commit hash, the repository URL, and a timestamp.
3. THE Signing_Script SHALL package the Binary_Artifact, the Attestation_Bundle, and the provenance manifest into a structured directory ready for Oras upload.

### Requirement 6: GHCR Upload via Oras

**User Story:** As a developer, I want the signed binary and attestation bundle uploaded to GHCR as an OCI artifact, so that consumers can pull and verify the attested binary.

#### Acceptance Criteria

1. THE Workflow SHALL install the Oras CLI.
2. THE Workflow SHALL authenticate to GHCR using the GitHub Actions token (`GITHUB_TOKEN`) via `oras login`.
3. WHEN the signing step completes successfully, THE Workflow SHALL push the Binary_Artifact as the primary layer of an OCI_Artifact to `ghcr.io/<owner>/<repo>/attested-hello:<tag>`.
4. THE Workflow SHALL attach the Attestation_Bundle as an additional layer with media type `application/vnd.attestation.bundle+tar.gz`.
5. THE Workflow SHALL attach the provenance manifest as an annotation or layer with media type `application/vnd.attestation.provenance+json`.
6. THE Workflow SHALL use the short commit SHA as the OCI_Artifact tag.
7. IF `oras push` fails, THEN THE Workflow SHALL fail the job and print the Oras error output to stderr.
8. WHEN the upload succeeds, THE Workflow SHALL print the full OCI reference (registry/repository:tag) and the manifest digest to the GitHub Actions job summary.

### Requirement 7: Attestation Document Artifact Upload

**User Story:** As a developer, I want all attestation documents saved as GitHub Actions artifacts, so that I can audit the attestation chain independently of the OCI artifact.

#### Acceptance Criteria

1. THE Workflow SHALL upload the `attestation-documents/` directory as a GitHub Actions artifact named `attestation-documents`.
2. THE Workflow SHALL upload attestation documents even when subsequent steps (signing or Oras upload) fail.
3. THE Workflow SHALL upload the provenance manifest as part of the `attestation-documents` artifact.

### Requirement 8: Project Configuration

**User Story:** As a developer, I want the project to be self-contained with proper dependency management, so that the workflow can install and run all required tools.

#### Acceptance Criteria

1. THE Rust_Project SHALL include a `pyproject.toml` that declares the Caller Python dependencies (requests, cbor2, pycose, pyOpenSSL, pycryptodome, cryptography, wolfcrypt).
2. THE Rust_Project SHALL include a `.gitignore` that excludes `target/`, `__pycache__/`, `.venv/`, `*.pyc`, and `attestation-documents/`.
3. THE Rust_Project SHALL include a `README.md` that documents the workflow inputs, the build-sign-upload pipeline, and how to verify the OCI artifact.
4. THE Workflow SHALL embed the AWS Nitro Attestation PKI root CA certificate as an environment variable, matching the certificate used by the `github-runner-ec2-attestation-caller` project.
5. THE Workflow SHALL embed the expected PCR values as an environment variable in JSON format.

### Requirement 9: GitHub Artifact Attestation

**User Story:** As a developer, I want the final OCI artifact on GHCR to have a GitHub Artifact Attestation, so that consumers can verify the artifact's supply-chain provenance using `gh attestation verify`.

#### Acceptance Criteria

1. WHEN the Oras upload to GHCR succeeds, THE Workflow SHALL use the `actions/attest@v4` GitHub Action with `subject-name` set to the fully-qualified OCI image name (without tag) and `subject-digest` set to the OCI manifest digest (in `sha256:<hex>` format) to generate a Sigstore-based provenance attestation. The action SHALL be configured with `push-to-registry: true` to attach the attestation to the OCI artifact in GHCR.
2. THE Workflow SHALL use the `id-token: write` and `attestations: write` permissions required by the `actions/attest@v4` action.
3. THE GitHub_Attestation SHALL bind the OCI_Artifact digest to the GitHub Actions workflow run, repository, and commit SHA.
4. WHEN the GitHub_Attestation step completes successfully, THE Workflow SHALL print a confirmation message to the GitHub Actions job summary indicating the attestation was created.
5. IF the GitHub_Attestation step fails, THEN THE Workflow SHALL use `continue-on-error: true` on the `actions/attest@v4` step and a subsequent step SHALL check the step outcome (`steps.<id>.outcome == 'failure'`) to emit a `::warning::` annotation and print a warning to the job summary, without failing the overall job.

### Requirement 10: Script Environment Variable Forwarding

**User Story:** As a developer, I want the GitHub Actions runtime environment variables forwarded to the build script running inside the Remote Executor's Execution_Container, so that the build script can authenticate to GHCR and upload the binary.

#### Acceptance Criteria

1. THE Caller module SHALL accept a `--script-env` CLI argument that can be specified multiple times, each providing a `KEY=VALUE` pair to forward as an environment variable to the Execution_Container.
2. THE Caller module SHALL include the collected `script_env` dictionary in the encrypted /execute payload alongside the existing fields (repository_url, commit_hash, script_path, github_token, oidc_token, nonce).
3. THE Workflow SHALL pass `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and the commit SHA to the Caller via `--script-env` arguments so they are forwarded to the Execution_Container.
4. THE Build_Script SHALL receive these environment variables as container environment variables inside the Execution_Container (where the project is mounted read-only at `/workspace` and `/tmp/` is the writable directory) and use them for GHCR authentication and binary upload.
5. IF any required environment variable (`GITHUB_TOKEN`, `GITHUB_REPOSITORY`) is not set in the Execution_Container, THEN THE Build_Script SHALL exit with a non-zero exit code and a descriptive error message.
6. THE Caller module SHALL compute a SHA-256 hex digest of the canonicalized `script_env` dictionary that was sent in the /execute payload, using the same canonicalization algorithm as the Remote Executor: sort keys lexicographically, serialize as JSON with compact separators (`(',', ':')`), no whitespace, then SHA-256 hex digest. WHEN `script_env` is empty or not provided, the digest SHALL be computed over `{}` (empty JSON object).
7. WHEN the execution-acceptance attestation contains a `user_data` field with a `script_env_hash` value, THE Caller module SHALL verify that the attested `script_env_hash` matches the locally computed digest.
8. IF the attested `script_env_hash` does not match the locally computed digest, THEN THE Caller module SHALL raise a CallerError with a descriptive message indicating the mismatch, including both the expected and attested values.
9. THE Caller module in the `github-runner-ec2-attestation-caller` project SHALL also be updated with the same `script_env_hash` verification logic.

### Requirement 11: Temporary GHCR Package Cleanup

**User Story:** As a developer, I want the temporary GHCR package created by the build script to be automatically deleted after the workflow completes, so that it does not pollute the package registry.

#### Acceptance Criteria

1. THE Workflow SHALL delete the Temporary_GHCR_Package after the final OCI artifact has been pushed (or after any failure), using the `actions/delete-package-versions@v5` GitHub Action.
2. THE cleanup step SHALL run with `if: always()` and `continue-on-error: true` to ensure the temporary package is removed regardless of whether the workflow succeeds or fails.
3. IF the cleanup step fails (e.g., package already deleted or permissions issue), THEN THE Workflow SHALL NOT fail the overall job.
4. THE cleanup step SHALL delete only the specific temporary tag to avoid affecting other temporary builds that may be running concurrently.
5. THE version ID lookup step SHALL support both user-owned and organization-owned repositories by trying the user packages API endpoint first and falling back to the organization packages API endpoint.
