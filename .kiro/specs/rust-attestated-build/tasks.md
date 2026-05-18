# Implementation Plan: Attested Rust Build Pipeline

## Overview

This plan implements a GitHub Actions workflow and supporting scripts that build a Rust binary inside an attested AWS Nitro Enclave environment, verify the binary's integrity, bundle it with attestation documents, push the result to GHCR as an OCI artifact via Oras, and create a GitHub Artifact Attestation for supply-chain provenance. The implementation copies the `call_remote_executor` Python module from the caller project and adds a Rust project, build script, workflow YAML, tests, and project configuration.

The binary is transferred from the enclave to the workflow via a temporary GHCR package (pushed by the build script using Oras and `GITHUB_TOKEN`), which is cleaned up after the workflow completes.

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
  - [x] 2.1 Copy `call_remote_executor` module to `.github/scripts/call_remote_executor/` and update `poll_output`
    - Copy all Python files from `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/`
    - Files: `__init__.py`, `__main__.py`, `cli.py`, `caller.py`, `encryption.py`, `attestation.py`, `artifact.py`, `errors.py`
    - In `caller.py` `poll_output` method: remove `"oidc_token": self._oidc_token or ""` from `plaintext_payload` (only send `nonce`)
    - In `caller.py` `poll_output` method: remove dead `if response.status_code == 401` and `if response.status_code == 403` blocks (server never returns 401/403 on `/output`)
    - In `caller.py` `poll_output` method: update docstring to state authentication is via Shared_Key possession (no OIDC token needed)
    - Apply the same `poll_output` changes to `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/caller.py`
    - _Requirements: 3.7_

  - [x] 2.2 Create Rust project structure at `rust-project/`
    - Create `rust-project/Cargo.toml` with binary target named `attested-hello`
    - Create `rust-project/src/main.rs` that prints a version string and build timestamp
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 3. Implement build script
  - [x] 3.1 Update `scripts/build-rust.sh` shell script for `/workspace` read-only constraint
    - Add `set -euo pipefail` for fail-fast behavior
    - Validate required environment variables: `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `COMMIT_SHA`
    - Install Rust toolchain via rustup if not present (set `RUSTUP_HOME=/tmp/.rustup` and `CARGO_HOME=/tmp/.cargo` since `/workspace` is read-only)
    - Copy Rust project source from `/workspace/rust-project/` to `/tmp/rust-project/` (since `/workspace` is read-only)
    - Run `cargo build --release` in `/tmp/rust-project/`
    - Compute SHA-256 of the binary at `/tmp/rust-project/target/release/attested-hello` and print `BINARY_SHA256:<hex_digest>` to stdout
    - Generate a unique tag: `<short-sha>-<random-suffix>` (e.g., `abc1234-x7k9m2`)
    - Install Oras CLI v1.3.2 (download tarball to `/tmp/`, extract binary to `/tmp/oras`, remove tarball)
    - Authenticate to GHCR: `echo $GITHUB_TOKEN | /tmp/oras login ghcr.io --username github --password-stdin --registry-config /tmp/oras-auth.json`
    - Push binary to GHCR with repository link annotation: `/tmp/oras push --registry-config /tmp/oras-auth.json ghcr.io/<GITHUB_REPOSITORY>/tmp-build:<tag> --annotation "org.opencontainers.image.source=https://github.com/<GITHUB_REPOSITORY>" /tmp/rust-project/target/release/attested-hello:application/octet-stream`
    - Print `BINARY_OCI_REF:<reference>` to stdout
    - Handle errors with descriptive stderr messages and non-zero exit codes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 4.1, 4.2, 4.3_

- [x] 4. Implement workflow helper scripts and parsing logic
  - [x] 4.1 Update `scripts/parse_markers.py` — Replace `BINARY_ARTIFACT_NAME` with `BINARY_OCI_REF`
    - Implement `parse_sha256_marker(stdout: str) -> str` that extracts `BINARY_SHA256:<value>`
    - Implement `parse_oci_ref_marker(stdout: str) -> str` that extracts `BINARY_OCI_REF:<value>`
    - Remove the old `parse_artifact_name_marker` function
    - Raise descriptive errors when markers are missing or malformed
    - _Requirements: 4.4, 4.8_

  - [x] 4.2 Create `scripts/validate_allowlist.py` — Python module for server URL allowlist validation
    - Implement `validate_server_url(server_url: str, allowlist: str) -> bool`
    - Accept URL if allowlist is empty; reject if URL not in comma-separated list (after trimming)
    - _Requirements: 3.4_

  - [x] 4.3 Create `scripts/create_provenance.py` — Python module for provenance manifest generation
    - Implement `create_provenance_manifest(binary_name, sha256, repo_url, commit_hash, run_id, timestamp) -> dict`
    - Return JSON-serializable dict matching the provenance manifest schema from the design
    - _Requirements: 5.2_

  - [x] 4.4 Update property test for stdout marker round-trip (Property 1)
    - **Property 1: Stdout marker round-trip**
    - For any valid marker value, embedding it in arbitrary stdout text and parsing back SHALL return the original value
    - Update to test `BINARY_OCI_REF` instead of `BINARY_ARTIFACT_NAME`
    - Use Hypothesis to generate random marker values and surrounding text
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 2.4, 2.5, 4.2, 4.3, 4.4**

  - [x] 4.5 Write property test for server URL allowlist validation (Property 2)
    - **Property 2: Server URL allowlist acceptance**
    - For any server URL and comma-separated allowlist, URL accepted iff it appears as exact match after trimming; empty allowlist accepts all
    - Use Hypothesis to generate random URLs and allowlists
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 3.4**

  - [x] 4.6 Write property test for provenance manifest completeness (Property 3)
    - **Property 3: Provenance manifest completeness**
    - For any valid build metadata inputs, the generated manifest SHALL contain all values in correct fields, and parsing back yields originals
    - Use Hypothesis to generate random build metadata
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 5.2**

  - [x] 4.7 Update unit tests for marker parsing, allowlist validation, and provenance manifest
    - Test `test_parse_sha256_marker` — parse known BINARY_SHA256 marker from sample stdout
    - Test `test_parse_oci_ref_marker` — parse known BINARY_OCI_REF marker
    - Test `test_missing_sha256_marker_raises` — error when marker missing
    - Test `test_missing_oci_ref_marker_raises` — error when marker missing
    - Test `test_sha256_mismatch_detected` — digest mismatch detection
    - Test `test_provenance_manifest_schema` — manifest matches expected schema
    - Test `test_allowlist_empty_accepts_all` — empty allowlist accepts any URL
    - Test `test_allowlist_rejects_unlisted` — URL not in allowlist is rejected
    - _Requirements: 2.4, 2.5, 3.4, 4.4, 4.7, 4.8, 5.2_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement GitHub Actions workflow
  - [x] 6.1 Create `.github/workflows/attested-rust-build.yml`
    - Define `workflow_dispatch` trigger with inputs: `server_url` (required), `script_path`, `commit_hash`, `repository_url`, `audience`, `server_url_allowlist`
    - Set permissions: `id-token: write`, `contents: read`, `packages: write`, `attestations: write`
    - Add `ROOT_CERT_PEM` and `EXPECTED_PCRS` environment variables matching the caller project
    - _Requirements: 3.1, 3.2, 8.4, 8.5_

  - [x] 6.2 Implement input validation step
    - Validate `server_url` is non-empty
    - Validate `server_url` against `server_url_allowlist` when provided
    - _Requirements: 3.3, 3.4_

  - [x] 6.3 Implement checkout, dependency installation, and Oras/GHCR setup steps
    - Checkout at `commit_hash` or current SHA
    - Install Python 3.11 and caller dependencies via `pip install -e ".[dev]"`
    - Install Oras CLI
    - Login to GHCR with `GITHUB_TOKEN` via `oras login`
    - _Requirements: 3.5, 3.6, 6.1, 6.2_

  - [x] 6.4 Implement caller invocation step
    - Invoke `python .github/scripts/call_remote_executor` with all required arguments
    - Capture stdout to file via `tee` for marker parsing
    - Pass `--attestation-output-dir attestation-documents`
    - Pass `--github-token` from `secrets.GITHUB_TOKEN`
    - Pass `--script-env` for `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and `COMMIT_SHA`
    - _Requirements: 3.7, 3.8, 10.3_

  - [x] 6.5 Implement binary retrieval and verification steps
    - Parse `BINARY_SHA256` and `BINARY_OCI_REF` from captured stdout
    - Pull temporary binary from GHCR via `oras pull` using the extracted OCI reference
    - Compute SHA-256 of downloaded binary and verify against expected digest
    - Fail with descriptive error on mismatch or missing markers
    - _Requirements: 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 6.6 Implement signing, packaging, and Oras upload steps
    - Create provenance manifest JSON
    - Package attestation bundle as tar.gz
    - Push binary + attestation bundle + provenance as OCI artifact to `ghcr.io/<owner>/<repo>/attested-hello:<short-sha>`
    - _Requirements: 5.1, 5.2, 5.3, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [x] 6.7 Implement GitHub Attestation and summary steps
    - Use `actions/attest@v4` with `subject-name` (fully-qualified OCI image name, no tag), `subject-digest` (OCI manifest digest in `sha256:<hex>` format), and `push-to-registry: true`
    - Set `continue-on-error: true` on the attest step and assign it a step `id` (e.g. `id: attest`)
    - Add a subsequent step that checks `steps.attest.outcome == 'failure'` and emits a `::warning::` annotation and prints a warning to `$GITHUB_STEP_SUMMARY`
    - Print OCI reference, manifest digest, and attestation status to `$GITHUB_STEP_SUMMARY`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 6.8 Implement temporary GHCR package cleanup steps
    - Add a `run` step with `if: always()` and `continue-on-error: true` that looks up the temporary package version ID by tag using `gh api` and the GitHub Packages REST API
    - Add `actions/delete-package-versions@v5` step with `if: always() && steps.<id>.outputs.version_id != ''` and `continue-on-error: true`
    - Pass the version ID via `package-version-ids`, set `package-name` and `package-type: container`
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [x] 6.9 Implement attestation document artifact upload step
    - Upload `attestation-documents/` as GitHub Actions artifact with `if: always()`
    - Include provenance manifest in the upload
    - _Requirements: 7.1, 7.2, 7.3, 3.9_

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Write smoke tests for static configuration
  - [x] 8.1 Write smoke tests for workflow YAML and project configuration
    - Test `test_workflow_yaml_inputs` — verify workflow_dispatch inputs in YAML
    - Test `test_workflow_yaml_permissions` — verify permissions in YAML
    - Test `test_workflow_yaml_root_cert` — verify ROOT_CERT_PEM env var in YAML
    - Test `test_workflow_yaml_expected_pcrs` — verify EXPECTED_PCRS env var in YAML
    - Test `test_workflow_yaml_cleanup_step` — verify cleanup steps: version ID lookup step and `actions/delete-package-versions@v5` step with `if: always()` and `continue-on-error: true`
    - Test `test_cargo_toml_binary_target` — verify Cargo.toml has attested-hello target
    - Test `test_pyproject_dependencies` — verify pyproject.toml has caller dependencies
    - Test `test_gitignore_patterns` — verify .gitignore has required patterns
    - _Requirements: 1.1, 3.1, 3.2, 8.1, 8.2, 8.4, 8.5, 11.1, 11.2_

- [x] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Update script environment variable forwarding
  - [x] 10.1 Verify `--script-env` CLI argument in the Caller module
    - The `--script-env` argument already exists in `cli.py` from the previous implementation
    - Verify it can be specified multiple times, each providing a `KEY=VALUE` pair
    - _Requirements: 10.1_

  - [x] 10.2 _(obsolete — superseded by 12.1 which adds script_env_hash verification on top of payload inclusion)_
    - _Requirements: 10.2_

  - [x] 10.3 Update workflow to pass simplified env vars via `--script-env`
    - In the "Run Remote Executor Caller" step of `attested-rust-build.yml`, pass `--script-env` arguments for `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and `COMMIT_SHA`
    - Remove the old `ACTIONS_RUNTIME_TOKEN` and `ACTIONS_RUNTIME_URL` forwarding (no longer needed)
    - _Requirements: 10.3, 10.4_

  - [x] 10.4 Update unit tests for `--script-env` argument parsing
    - Verify existing tests still pass with the simplified env var set
    - Update any tests that referenced `ACTIONS_RUNTIME_TOKEN` or `ACTIONS_RUNTIME_URL`
    - _Requirements: 10.1, 10.2_

- [x] 11. Checkpoint - Ensure all tests pass after caller module updates
  - Ensure all tests pass (script_env forwarding + poll_output OIDC removal), ask the user if questions arise.

- [x] 12. Implement script_env_hash verification in Caller module
  - [x] 12.1 Add `_compute_script_env_hash` helper to `caller.py` in both projects
    - Implement in `.github/scripts/call_remote_executor/caller.py`
    - Canonicalization: `json.dumps(script_env, sort_keys=True, separators=(',', ':'))` then `hashlib.sha256(...).hexdigest()`
    - When `script_env` is empty or None, compute hash of `{}`
    - Apply the same change to `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/caller.py`
    - _Requirements: 10.6_

  - [x] 12.2 Add `script_env_hash` verification to `execute()` method request binding
    - In the existing request-binding loop in `execute()` (after `for field in ("repository_url", "commit_hash", "script_path")`), add verification of `script_env_hash`
    - Compute expected hash from `script_env` passed to `execute()`
    - Compare against `attested.get("script_env_hash")`
    - Raise CallerError on mismatch with expected and attested values
    - Apply the same change to `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/caller.py`
    - _Requirements: 10.7, 10.8, 10.9_

  - [x] 12.3 Write property test for script_env_hash round-trip (Property 4)
    - **Property 4: script_env_hash round-trip**
    - For any dictionary of string key-value pairs, the hash is deterministic and matches the canonical algorithm
    - Use Hypothesis to generate random string dicts
    - Minimum 100 examples via `@settings(max_examples=100)`
    - **Validates: Requirements 10.6, 10.7**

  - [x] 12.4 Write unit tests for script_env_hash verification
    - Test `test_script_env_hash_empty_dict` — empty dict produces sha256("{}")
    - Test `test_script_env_hash_known_value` — known dict produces expected hash
    - Test `test_script_env_hash_mismatch_raises` — CallerError raised on hash mismatch
    - _Requirements: 10.6, 10.7, 10.8_

- [x] 13. Checkpoint - Ensure all tests pass after script_env_hash verification
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. Adopt upstream security hardening: execution_id binding, encrypted error envelopes, mandatory nonces

  - [x] 14.1 Add `execution_id` verification to `execute()` request binding
    - In `.github/scripts/call_remote_executor/caller.py` `execute()` method, after the `script_env_hash` verification block, add verification of `execution_id`
    - Compare `attested.get("execution_id")` against `decrypted.get("execution_id")`
    - Raise CallerError on mismatch with descriptive message including both values
    - _Requirements: 10.10, 10.11_

  - [x] 14.2 Add encrypted error envelope detection to `execute()`
    - In `.github/scripts/call_remote_executor/caller.py` `execute()` method, after decrypting the HTTP 200 response, check if the decrypted payload contains an `error` field
    - If `error` field is present, raise CallerError with the `error` message and `error_code` from the envelope (do NOT proceed to attestation validation)
    - This check must occur BEFORE the attestation_document extraction
    - _Requirements: 10.12, 10.13_

  - [x] 14.3 Add encrypted error envelope detection to `poll_output()`
    - In `.github/scripts/call_remote_executor/caller.py` `poll_output()` method, after decrypting the HTTP 200 response, check if the decrypted payload contains an `error` field
    - If `error` field is present, raise CallerError with the `error` message and `error_code` from the envelope
    - This check must occur BEFORE processing stdout/stderr/exit_code
    - _Requirements: 10.14_

  - [x] 14.4 _(skipped — not needed)_ Pre-decryption errors still use plaintext HTTP; only post-decryption errors use encrypted envelopes. The existing handlers cover pre-decryption 400/413 and the edge case where the server hasn't been upgraded yet.
    - _Requirements: 10.13_

  - [ ] 14.5 Write unit tests for execution_id binding and encrypted error envelopes
    - Test `test_execution_id_binding_verified` — verify attested execution_id matches response body execution_id (happy path)
    - Test `test_execution_id_mismatch_raises` — verify CallerError raised when attested execution_id differs from response body
    - Test `test_encrypted_error_envelope_detected` — verify CallerError raised when decrypted /execute response contains `error` field
    - Test `test_encrypted_error_envelope_on_poll` — verify CallerError raised when decrypted /output response contains `error` field
    - _Requirements: 10.10, 10.11, 10.12, 10.14_

- [ ] 15. Checkpoint - Ensure all tests pass after security hardening adoption
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The caller module is copied as-is from `github-runner-ec2-attestation-caller/.github/scripts/call_remote_executor/`
- Tests use Python/pytest/Hypothesis as specified in the design
- The build script uses shell (bash) as it runs on the Remote Executor
- The Rust project is minimal — just enough to produce a binary for the demo
- The build script uses Oras CLI v1.3.2 to push to GHCR — this only requires `GITHUB_TOKEN` (no `ACTIONS_RUNTIME_TOKEN` or `ACTIONS_RUNTIME_URL`). The build script downloads and installs Oras entirely within `/tmp/` (the only writable directory in the enclave), using `--registry-config /tmp/oras-auth.json` for credential storage. The `org.opencontainers.image.source` annotation is included to link the package to the repository for `GITHUB_TOKEN` delete permissions.
- `/workspace` is mounted read-only inside the Execution_Container. The build script must copy source from `/workspace/rust-project/` to `/tmp/rust-project/` before compiling, and set `RUSTUP_HOME`/`CARGO_HOME` to `/tmp/` paths.
- The temporary GHCR package is cleaned up via `actions/delete-package-versions@v5` after the workflow completes
- Tasks from the previous implementation that are unchanged (project structure, caller module copy, Rust project, allowlist validation, provenance manifest) are marked as `[x]` (already done)
- The `/output` endpoint on the Remote Executor no longer requires or validates an OIDC token (upstream commit b846e4b). Authentication is provided solely by possession of the execution-bound Shared_Key established during the PQ_Hybrid_KEM exchange on `/execute`. The caller's `poll_output` method should send only `nonce` in the encrypted payload. The server returns 400 for decryption failures and 404 for unknown execution IDs — it does not return 401/403 on this endpoint, making the caller's 401/403 handling dead code.
- Upstream security hardening (commit c667730) introduces three changes affecting the caller: (1) `execution_id` is now included in attestation user_data — the caller must verify it matches the response body; (2) post-decryption application errors are returned as encrypted error envelopes (HTTP 200 with `{"error": ..., "error_code": ...}` in the decrypted payload) — the caller must detect and handle these; (3) nonces are now mandatory on /execute and /output — the caller already sends them, so no code change needed for this.
- The existing plaintext HTTP error handlers (400, 401, 403, 413, 503) in `execute()` remain valid for pre-decryption errors (malformed JSON, decryption failure, body size exceeded). Post-decryption errors (OIDC failures, repo mismatch, nonce duplicate, etc.) are now returned as encrypted envelopes with HTTP 200.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 1, "tasks": ["14.1", "14.2", "14.3"] },
    { "id": 2, "tasks": ["14.4"] },
    { "id": 3, "tasks": ["14.5"] },
    { "id": 4, "tasks": ["15"] }
  ]
}
```
