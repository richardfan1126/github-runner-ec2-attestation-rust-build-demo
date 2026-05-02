# Implementation Plan: Attested Rust Build Pipeline

## Overview

This plan implements a GitHub Actions workflow and supporting scripts that build a Rust binary inside an attested AWS Nitro Enclave environment, verify the binary's integrity, bundle it with attestation documents, push the result to GHCR as an OCI artifact via Oras, and create a GitHub Artifact Attestation for supply-chain provenance. The implementation copies the `call_remote_executor` Python module from the caller project and adds a Rust project, build script, workflow YAML, tests, and project configuration.

## Tasks

- [x] 1. Set up project structure and configuration files
  - [x] 1.1 Create `pyproject.toml` with caller dependencies and dev dependencies
    - Declare dependencies: requests, cbor2, pycose, pyOpenSSL, pycryptodome, cryptography, wolfcrypt
    - Declare dev dependencies: hypothesis>=6.0.0, pytest>=7.0.0, pyyaml>=6.0.0
    - Configure hatch build to include `.github/scripts/call_remote_executor`
    - Configure pytest testpaths to `tests/`
    - _Requirements: 8.1_

  - [x] 1.2 Create `.gitignore` with required exclusion patterns
    - Exclude `target/`, `__pycache__/`, `.venv/`, `*.pyc`, `attestation-documents/`
    - Also exclude `.hypothesis/`, `.pytest_cache/`, `*.egg-info/`
    - _Requirements: 8.2_

  - [x] 1.3 Create `README.md` documenting the project
    - Document workflow inputs and how to trigger the workflow
    - Document the build-sign-upload pipeline flow
    - Document how to verify the OCI artifact using `oras pull` and `gh attestation verify`
    - _Requirements: 8.3_

- [x] 2. Copy caller module and create Rust project
  - [x] 2.1 Copy `call_remote_executor` module to `.github/scripts/call_remote_executor/`
    - Copy all Python files from `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/`
    - Files: `__init__.py`, `__main__.py`, `cli.py`, `caller.py`, `encryption.py`, `attestation.py`, `artifact.py`, `errors.py`
    - _Requirements: 3.7_

  - [x] 2.2 Create Rust project structure at `rust-project/`
    - Create `rust-project/Cargo.toml` with binary target named `attested-hello`
    - Create `rust-project/src/main.rs` that prints a version string and build timestamp
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 3. Implement build script
  - [x] 3.1 Create `scripts/build-rust.sh` shell script
    - Add `set -euo pipefail` for fail-fast behavior
    - Install Rust toolchain via rustup if not present
    - Run `cargo build --release` in the `rust-project/` directory
    - Compute SHA-256 of the binary and print `BINARY_SHA256:<hex_digest>` to stdout
    - Upload binary to GitHub Actions Artifacts using `github_token` and the v3 pipeline artifacts REST API via curl (the v4 API requires the `@actions/artifact` Node.js package, which is unavailable in the enclave)
    - Print `BINARY_ARTIFACT_NAME:<name>` to stdout
    - Handle errors with descriptive stderr messages and non-zero exit codes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 4.1, 4.2, 4.3_

- [ ] 4. Implement workflow helper scripts and parsing logic
  - [ ] 4.1 Create `scripts/parse_markers.py` — Python module for parsing stdout markers
    - Implement `parse_sha256_marker(stdout: str) -> str` that extracts `BINARY_SHA256:<value>`
    - Implement `parse_artifact_name_marker(stdout: str) -> str` that extracts `BINARY_ARTIFACT_NAME:<value>`
    - Raise descriptive errors when markers are missing or malformed
    - _Requirements: 4.4, 4.8_

  - [ ] 4.2 Create `scripts/validate_allowlist.py` — Python module for server URL allowlist validation
    - Implement `validate_server_url(server_url: str, allowlist: str) -> bool`
    - Accept URL if allowlist is empty; reject if URL not in comma-separated list (after trimming)
    - _Requirements: 3.4_

  - [ ] 4.3 Create `scripts/create_provenance.py` — Python module for provenance manifest generation
    - Implement `create_provenance_manifest(binary_name, sha256, repo_url, commit_hash, run_id, timestamp) -> dict`
    - Return JSON-serializable dict matching the provenance manifest schema from the design
    - _Requirements: 5.2_

  - [ ] 4.4 Write property test for stdout marker round-trip (Property 1)
    - **Property 1: Stdout marker round-trip**
    - For any valid marker value, embedding it in arbitrary stdout text and parsing back SHALL return the original value
    - Use Hypothesis to generate random marker values and surrounding text
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 2.4, 2.5, 4.2, 4.3, 4.4**

  - [ ] 4.5 Write property test for server URL allowlist validation (Property 2)
    - **Property 2: Server URL allowlist acceptance**
    - For any server URL and comma-separated allowlist, URL accepted iff it appears as exact match after trimming; empty allowlist accepts all
    - Use Hypothesis to generate random URLs and allowlists
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 3.4**

  - [ ] 4.6 Write property test for provenance manifest completeness (Property 3)
    - **Property 3: Provenance manifest completeness**
    - For any valid build metadata inputs, the generated manifest SHALL contain all values in correct fields, and parsing back yields originals
    - Use Hypothesis to generate random build metadata
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 5.2**

  - [ ] 4.7 Write unit tests for marker parsing, allowlist validation, and provenance manifest
    - Test `test_parse_sha256_marker` — parse known BINARY_SHA256 marker from sample stdout
    - Test `test_parse_artifact_name_marker` — parse known BINARY_ARTIFACT_NAME marker
    - Test `test_missing_sha256_marker_raises` — error when marker missing
    - Test `test_missing_artifact_name_marker_raises` — error when marker missing
    - Test `test_sha256_mismatch_detected` — digest mismatch detection
    - Test `test_provenance_manifest_schema` — manifest matches expected schema
    - Test `test_allowlist_empty_accepts_all` — empty allowlist accepts any URL
    - Test `test_allowlist_rejects_unlisted` — URL not in allowlist is rejected
    - _Requirements: 2.4, 2.5, 3.4, 4.4, 4.7, 4.8, 5.2_

- [ ] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement GitHub Actions workflow
  - [ ] 6.1 Create `.github/workflows/attested-rust-build.yml`
    - Define `workflow_dispatch` trigger with inputs: `server_url` (required), `script_path`, `commit_hash`, `repository_url`, `audience`, `server_url_allowlist`
    - Set permissions: `id-token: write`, `contents: read`, `packages: write`, `attestations: write`
    - Add `ROOT_CERT_PEM` and `EXPECTED_PCRS` environment variables matching the caller project
    - _Requirements: 3.1, 3.2, 8.4, 8.5_

  - [ ] 6.2 Implement input validation step
    - Validate `server_url` is non-empty
    - Validate `server_url` against `server_url_allowlist` when provided
    - _Requirements: 3.3, 3.4_

  - [ ] 6.3 Implement checkout and dependency installation steps
    - Checkout at `commit_hash` or current SHA
    - Install Python 3.11 and caller dependencies via `pip install -e ".[dev]"`
    - _Requirements: 3.5, 3.6_

  - [ ] 6.4 Implement caller invocation step
    - Invoke `python .github/scripts/call_remote_executor` with all required arguments
    - Capture stdout to file via `tee` for marker parsing
    - Pass `--attestation-output-dir attestation-documents`
    - Pass `--github-token` from `secrets.GITHUB_TOKEN`
    - _Requirements: 3.7, 3.8_

  - [ ] 6.5 Implement binary retrieval and verification steps
    - Parse `BINARY_SHA256` and `BINARY_ARTIFACT_NAME` from captured stdout
    - Download binary artifact via `actions/download-artifact@v4`
    - Compute SHA-256 of downloaded binary and verify against expected digest
    - Fail with descriptive error on mismatch or missing markers
    - _Requirements: 4.4, 4.5, 4.6, 4.7, 4.8_

  - [ ] 6.6 Implement signing, packaging, and Oras upload steps
    - Create provenance manifest JSON
    - Package attestation bundle as tar.gz
    - Install Oras CLI
    - Login to GHCR with `GITHUB_TOKEN`
    - Push binary + attestation bundle + provenance as OCI artifact to `ghcr.io/<owner>/<repo>/attested-hello:<short-sha>`
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [ ] 6.7 Implement GitHub Attestation and summary steps
    - Use `actions/attest@v4` with `subject-name` (fully-qualified OCI image name, no tag), `subject-digest` (OCI manifest digest in `sha256:<hex>` format), and `push-to-registry: true`
    - Set `continue-on-error: true` on the attest step and assign it a step `id` (e.g. `id: attest`)
    - Add a subsequent step that checks `steps.attest.outcome == 'failure'` and emits a `::warning::` annotation and prints a warning to `$GITHUB_STEP_SUMMARY`
    - Print OCI reference, manifest digest, and attestation status to `$GITHUB_STEP_SUMMARY`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ] 6.8 Implement attestation document artifact upload step
    - Upload `attestation-documents/` as GitHub Actions artifact with `if: always()`
    - Include provenance manifest in the upload
    - _Requirements: 7.1, 7.2, 7.3, 3.9_

- [ ] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Write smoke tests for static configuration
  - [ ] 8.1 Write smoke tests for workflow YAML and project configuration
    - Test `test_workflow_yaml_inputs` — verify workflow_dispatch inputs in YAML
    - Test `test_workflow_yaml_permissions` — verify permissions in YAML
    - Test `test_workflow_yaml_root_cert` — verify ROOT_CERT_PEM env var in YAML
    - Test `test_workflow_yaml_expected_pcrs` — verify EXPECTED_PCRS env var in YAML
    - Test `test_cargo_toml_binary_target` — verify Cargo.toml has attested-hello target
    - Test `test_pyproject_dependencies` — verify pyproject.toml has caller dependencies
    - Test `test_gitignore_patterns` — verify .gitignore has required patterns
    - _Requirements: 1.1, 3.1, 3.2, 8.1, 8.2, 8.4, 8.5_

- [ ] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The caller module is copied as-is from `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/`
- Tests use Python/pytest/Hypothesis as specified in the design
- The build script uses shell (bash) as it runs on the Remote Executor
- The Rust project is minimal — just enough to produce a binary for the demo
