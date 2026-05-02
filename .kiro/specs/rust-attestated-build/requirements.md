# Requirements Document

## Introduction

This feature delivers a GitHub Actions workflow and supporting scripts that build a Rust binary on a remote attested executor (using the `github-runner-ec2-attestation` system), sign the built binary with the attestation document retrieved from the remote executor, and upload the signed binary as an OCI artifact to GitHub Container Registry (GHCR) using the Oras CLI. The project is a standalone demo that reuses the caller communication pattern from `github-runner-ec2-attestation-caller`.

## Glossary

- **Workflow**: A GitHub Actions workflow definition (YAML) that orchestrates the end-to-end build, sign, and upload pipeline.
- **Caller**: The Python-based client (`call_remote_executor` module) that communicates with the Remote Executor server via the attested channel (health check, OIDC, attestation, PQ_Hybrid_KEM, encrypted execution, polling).
- **Remote_Executor**: The server-side component running on an AWS Nitro Enclave that executes scripts in an attested environment and returns output with attestation documents.
- **Build_Script**: A shell script committed to the repository that the Remote_Executor clones and runs to compile the Rust binary.
- **Attestation_Document**: A COSE Sign1 document produced by the AWS Nitro Enclave that cryptographically proves the execution environment identity and integrity.
- **Binary_Artifact**: The compiled Rust executable produced by the Build_Script on the Remote_Executor.
- **Signing_Script**: A script that attaches the Attestation_Document to the Binary_Artifact as a cryptographic provenance record.
- **GHCR**: GitHub Container Registry, an OCI-compliant registry at `ghcr.io`.
- **Oras**: The OCI Registry As Storage CLI tool used to push and pull arbitrary artifacts to OCI registries.
- **OCI_Artifact**: An artifact stored in an OCI registry, consisting of the Binary_Artifact and its associated Attestation_Document as layers or annotations.
- **Rust_Project**: A minimal Rust project (Cargo.toml and source files) that compiles into the Binary_Artifact.
- **Attestation_Bundle**: A directory containing the server-identity, execution-acceptance, and output-integrity attestation documents and their manifest, produced by the Caller during execution.
- **Temporary_Store**: GitHub Actions Artifacts, used to transfer the Binary_Artifact out of the attested environment. The Remote_Executor uses the `github_token` (received in the encrypted execution payload) to upload artifacts via the GitHub Actions Artifacts API, and the Workflow downloads them using the `actions/download-artifact` action.
- **GitHub_Attestation**: A Sigstore-based attestation created by `gh attestation` that binds a build artifact to the GitHub Actions workflow run, providing supply-chain provenance via GitHub's artifact attestation feature.

## Requirements

### Requirement 1: Rust Project Structure

**User Story:** As a developer, I want a minimal Rust project in the repository, so that the Remote_Executor can compile it into a Binary_Artifact.

#### Acceptance Criteria

1. THE Rust_Project SHALL contain a valid `Cargo.toml` with a binary target named `attested-hello`.
2. THE Rust_Project SHALL contain a `src/main.rs` that compiles into a statically-linked executable.
3. WHEN compiled with `cargo build --release`, THE Rust_Project SHALL produce a single Binary_Artifact at `target/release/attested-hello`.
4. THE Binary_Artifact SHALL print a version string and a build timestamp to stdout when executed.

### Requirement 2: Remote Build Script

**User Story:** As a developer, I want a build script that compiles the Rust project on the Remote_Executor, so that the binary is built inside the attested environment.

#### Acceptance Criteria

1. THE Build_Script SHALL be a shell script located at `scripts/build-rust.sh` in the repository.
2. WHEN executed by the Remote_Executor, THE Build_Script SHALL install the Rust toolchain if not already present.
3. WHEN executed by the Remote_Executor, THE Build_Script SHALL run `cargo build --release` in the Rust_Project directory.
4. WHEN the build succeeds, THE Build_Script SHALL compute a SHA-256 digest of the Binary_Artifact and print it to stdout in the format `BINARY_SHA256:<hex_digest>`.
5. WHEN the build succeeds, THE Build_Script SHALL upload the Binary_Artifact to GitHub Actions Artifacts using the `github_token` and the GitHub Actions Artifacts API, and print the artifact name to stdout in the format `BINARY_ARTIFACT_NAME:<name>`.
6. IF the Rust toolchain installation fails, THEN THE Build_Script SHALL exit with a non-zero exit code and print a descriptive error to stderr.
7. IF `cargo build --release` fails, THEN THE Build_Script SHALL exit with a non-zero exit code and print the compiler error output to stderr.
8. IF the upload to GitHub Actions Artifacts fails, THEN THE Build_Script SHALL exit with a non-zero exit code and print a descriptive error to stderr.

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

### Requirement 4: Binary Upload from Executor and Retrieval in Workflow

**User Story:** As a developer, I want the build script to upload the compiled binary to GitHub Actions Artifacts from the executor, so that the workflow can download, sign, and publish it.

#### Acceptance Criteria

1. WHEN the build succeeds, THE Build_Script SHALL upload the Binary_Artifact to GitHub Actions Artifacts using the `github_token` received in the encrypted execution payload.
2. THE Build_Script SHALL print the artifact name to stdout in the format `BINARY_ARTIFACT_NAME:<name>`.
3. THE Build_Script SHALL print the SHA-256 digest of the Binary_Artifact to stdout in the format `BINARY_SHA256:<hex_digest>`.
4. WHEN the Caller completes successfully, THE Workflow SHALL parse the execution stdout to extract the `BINARY_ARTIFACT_NAME` and `BINARY_SHA256` values.
5. THE Workflow SHALL download the Binary_Artifact from GitHub Actions Artifacts using the `actions/download-artifact` action with the extracted artifact name.
6. THE Workflow SHALL compute a SHA-256 digest of the downloaded Binary_Artifact and verify it matches the `BINARY_SHA256` value from the build output.
7. IF the SHA-256 digest of the downloaded Binary_Artifact does not match the `BINARY_SHA256` value, THEN THE Workflow SHALL fail with a descriptive integrity mismatch error.
8. IF the `BINARY_ARTIFACT_NAME` or `BINARY_SHA256` markers are missing from the execution stdout, THEN THE Workflow SHALL fail with a descriptive error message.

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

1. WHEN the Oras upload to GHCR succeeds, THE Workflow SHALL run `gh attestation create` (or the `actions/attest` action) against the uploaded OCI_Artifact to generate a Sigstore-based provenance attestation.
2. THE Workflow SHALL use the `id-token: write` and `attestations: write` permissions required by the GitHub Attestation feature.
3. THE GitHub_Attestation SHALL bind the OCI_Artifact digest to the GitHub Actions workflow run, repository, and commit SHA.
4. WHEN the GitHub_Attestation step completes, THE Workflow SHALL print a confirmation message to the GitHub Actions job summary indicating the attestation was created.
5. IF the GitHub_Attestation step fails, THEN THE Workflow SHALL print a warning but not fail the overall job, since the OCI_Artifact and Nitro attestation bundle are already uploaded.
