# attested-build-workflow Specification

## Purpose

Define the GitHub Actions workflow that orchestrates the end-to-end attested
build: dispatching a remote build on the attested Remote Executor, retrieving the
compiled binary from the temporary GHCR package and verifying its integrity,
signing it with the attestation bundle, publishing the final attested OCI artifact
to GHCR, generating a GitHub artifact attestation, uploading the attestation
documents as workflow artifacts, and cleaning up the temporary package.

The build that runs inside the executor is specified in
[[hardened-build-environment]]; the attested-channel client this workflow invokes
is specified in [[attested-executor-caller]].

## Requirements

### Requirement: Workflow Dispatch Inputs and Permissions

The workflow SHALL be triggered by `workflow_dispatch` with the defined inputs and
SHALL request the permissions required for OIDC, package publishing, and
attestation.

#### Scenario: Dispatch inputs are defined

- **WHEN** the workflow is dispatched
- **THEN** it accepts `server_url` (required), `script_path` (optional, default `scripts/build-rust.sh`), `commit_hash` (optional, default current SHA), `repository_url` (optional, default current repository), `audience` (optional), and `server_url_allowlist` (optional)

#### Scenario: Required permissions are requested

- **WHEN** the workflow runs
- **THEN** it requests `id-token: write`, `contents: read`, `packages: write`, and `attestations: write`

### Requirement: Server URL Validation and Allowlisting

The workflow SHALL validate `server_url` before proceeding and SHALL enforce an
optional allowlist.

#### Scenario: Empty server_url is rejected

- **WHEN** `server_url` is empty
- **THEN** the workflow fails before contacting any executor

#### Scenario: Allowlist is enforced when provided

- **WHEN** `server_url_allowlist` is provided and `server_url` is not in the comma-separated list
- **THEN** the workflow rejects the request

### Requirement: Remote Attested Build Invocation

The workflow SHALL check out the repository at the requested commit, install the
caller's dependencies, and invoke the caller to run the build on the Remote
Executor.

#### Scenario: Caller is invoked with attestation parameters

- **WHEN** the workflow proceeds past validation
- **THEN** it checks out the repository at the specified `commit_hash` (or current SHA), installs Python 3.11 and the caller dependencies from `pyproject.toml`, and invokes the caller with the configured inputs, `--root-cert-pem`, `--expected-pcrs`, and `--attestation-output-dir attestation-documents`

#### Scenario: commit_hash is bound to the OIDC sha claim

- **WHEN** the workflow passes `commit_hash` to the caller
- **THEN** it passes the current workflow's `github.sha` (or leaves the default) so the executor's server-side check that `commit_hash` matches the OIDC token's `sha` claim succeeds rather than being rejected with HTTP 403

#### Scenario: Caller failure fails the job but preserves attestations

- **WHEN** the caller exits non-zero
- **THEN** the workflow fails the job and still uploads any available attestation documents as artifacts

### Requirement: Binary Retrieval and Integrity Verification

After a successful remote build, the workflow SHALL retrieve the binary from the
temporary GHCR package and verify its digest matches the build output.

#### Scenario: Markers are parsed and the binary is pulled

- **WHEN** the caller completes successfully
- **THEN** the workflow parses the execution stdout for `BINARY_OCI_REF` and `BINARY_SHA256`, then pulls the binary from GHCR via oras using the extracted reference

#### Scenario: Digest mismatch fails the job

- **WHEN** the SHA-256 of the downloaded binary does not match the `BINARY_SHA256` from the build output
- **THEN** the workflow fails with a descriptive integrity-mismatch error

#### Scenario: Missing markers fail the job

- **WHEN** either `BINARY_OCI_REF` or `BINARY_SHA256` is absent from the execution stdout
- **THEN** the workflow fails with a descriptive error

### Requirement: Attestation-Based Signing

The workflow SHALL assemble the attestation bundle and a provenance manifest and
package them with the binary for upload.

#### Scenario: Attestation bundle and provenance manifest are produced

- **WHEN** the signing step runs
- **THEN** it creates an attestation-bundle directory containing the server-identity, execution-acceptance, and output-integrity attestation documents, and a JSON provenance manifest including the binary digest, attestation document references, commit hash, repository URL, and timestamp

#### Scenario: Artifact is staged for upload

- **WHEN** signing completes
- **THEN** the binary, the attestation bundle, and the provenance manifest are packaged into a structured directory ready for oras upload

### Requirement: Final OCI Artifact Upload to GHCR

The workflow SHALL publish the signed binary and attestation bundle to GHCR as an
OCI artifact via oras.

#### Scenario: Artifact is pushed with bundle and provenance layers

- **WHEN** signing succeeds
- **THEN** the workflow authenticates to GHCR with `GITHUB_TOKEN` via oras and pushes the binary as the primary layer to `ghcr.io/<owner>/<repo>/attested-hello:<short-sha>`, attaching the attestation bundle with media type `application/vnd.attestation.bundle+tar.gz` and the provenance manifest with media type `application/vnd.attestation.provenance+json`

#### Scenario: Push failure fails the job

- **WHEN** `oras push` fails
- **THEN** the workflow fails the job and prints the oras error to stderr

#### Scenario: Successful push is summarized

- **WHEN** the upload succeeds
- **THEN** the workflow prints the full OCI reference and the manifest digest to the job summary

### Requirement: Attestation Document Artifact Upload

The workflow SHALL upload the attestation documents as a GitHub Actions artifact,
even when later steps fail.

#### Scenario: Attestation documents are uploaded regardless of outcome

- **WHEN** the workflow runs
- **THEN** it uploads the `attestation-documents/` directory (including the provenance manifest) as an artifact named `attestation-documents`, even when the signing or oras-upload steps fail

### Requirement: GitHub Artifact Attestation

The workflow SHALL generate a Sigstore-based GitHub artifact attestation binding
the published OCI artifact to the workflow run, without failing the job if that
step fails.

#### Scenario: Attestation is created and pushed to the registry

- **WHEN** the oras upload to GHCR succeeds
- **THEN** the workflow uses `actions/attest@v4` with `subject-name` set to the fully-qualified OCI image name (without tag), `subject-digest` set to the OCI manifest digest (`sha256:<hex>`), and `push-to-registry: true`, binding the artifact digest to the workflow run, repository, and commit SHA, and prints a confirmation to the job summary

#### Scenario: Attestation failure warns without failing the job

- **WHEN** the `actions/attest@v4` step fails
- **THEN** `continue-on-error: true` keeps the job running, and a subsequent step detects the failed outcome to emit a `::warning::` annotation and a job-summary warning without failing the overall job

### Requirement: Temporary GHCR Package Cleanup

The workflow SHALL delete the temporary GHCR package after completion, regardless
of success or failure, without failing the job.

#### Scenario: Temporary package is deleted on success or failure

- **WHEN** the workflow finishes (after the final artifact push or any failure)
- **THEN** a cleanup step running with `if: always()` and `continue-on-error: true` deletes only the specific temporary tag via `actions/delete-package-versions@v5`, and a failed cleanup (e.g. already deleted or permissions) does not fail the job

#### Scenario: Cleanup supports user- and org-owned repositories

- **WHEN** the cleanup resolves the package version ID
- **THEN** it tries the user packages API endpoint first and falls back to the organization packages API endpoint

### Requirement: Project Configuration

The repository SHALL be self-contained with the dependency, ignore, documentation,
and embedded-trust configuration the workflow needs.

#### Scenario: Dependencies and ignores are declared

- **WHEN** the project is inspected
- **THEN** `pyproject.toml` declares the caller's Python dependencies (requests, cbor2, pycose, pyOpenSSL, pycryptodome, cryptography, wolfcrypt) and `.gitignore` excludes `target/`, `__pycache__/`, `.venv/`, `*.pyc`, and `attestation-documents/`

#### Scenario: Trust anchors are embedded in the workflow

- **WHEN** the workflow runs the caller
- **THEN** it embeds the AWS Nitro Attestation PKI root CA certificate and the expected PCR values (JSON) as environment variables, matching the `github-runner-ec2-attestation-caller` project

#### Scenario: README documents the pipeline

- **WHEN** a developer reads the repository
- **THEN** the README documents the workflow inputs, the build-sign-upload pipeline, and how to verify the OCI artifact
