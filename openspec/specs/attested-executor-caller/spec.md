# attested-executor-caller Specification

## Purpose

Define the Python caller (`call_remote_executor`) that communicates with the
Remote Executor over its attested channel and verifies the attestation chain. The
caller forwards the environment the build needs, proves the execution-acceptance
attestation binds to the exact request it sent, surfaces server-side errors that
arrive as encrypted envelopes, and obtains and verifies an output-integrity
attestation after the build completes.

This capability is invoked by [[attested-build-workflow]] and forwards the
environment consumed by the build in [[hardened-build-environment]].

## Requirements

### Requirement: Script Environment Forwarding

The caller SHALL forward caller-supplied environment variables into the execution
container via the encrypted `/execute` payload.

#### Scenario: Env vars are collected and forwarded

- **WHEN** the caller is invoked with one or more `--script-env KEY=VALUE` arguments
- **THEN** it collects them into a `script_env` dictionary and includes that dictionary in the encrypted `/execute` payload alongside the existing fields (repository_url, commit_hash, script_path, github_token, oidc_token, nonce), with a non-empty `nonce` (the executor rejects missing/empty nonces with HTTP 400)

#### Scenario: Build credentials are forwarded

- **WHEN** the workflow invokes the caller
- **THEN** it passes `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and the commit SHA via `--script-env` so they reach the execution container for GHCR authentication and binary upload

### Requirement: Execution-Acceptance Attestation Verification

The caller SHALL verify that the execution-acceptance attestation binds to the
exact request it sent, detecting server-side tampering of the execution
parameters.

#### Scenario: script_env_hash is verified

- **WHEN** the execution-acceptance attestation's `user_data` contains a `script_env_hash`
- **THEN** the caller computes the SHA-256 of the canonicalized `script_env` it sent (keys sorted lexicographically, compact JSON separators `(',', ':')`, no whitespace; `{}` when empty), and verifies the attested hash matches — raising a CallerError naming both expected and attested values on mismatch

#### Scenario: execution_id is verified

- **WHEN** the execution-acceptance attestation's `user_data` contains an `execution_id`
- **THEN** the caller verifies it matches the `execution_id` in the decrypted `/execute` response body, raising a CallerError describing the mismatch otherwise

### Requirement: Encrypted Error Envelope Handling

The caller SHALL detect and surface server-side application errors returned as
encrypted envelopes rather than misinterpreting them as successful responses.

#### Scenario: Encrypted error envelope on /execute is surfaced

- **WHEN** the executor returns HTTP 200 on `/execute` whose decrypted payload contains an `error` field
- **THEN** the caller treats it as an encrypted error envelope and raises a CallerError with the `error` message and `error_code`, rather than treating it as a successful execution

#### Scenario: Pre- and post-decryption errors are distinguished

- **WHEN** an error occurs
- **THEN** the caller distinguishes pre-decryption plaintext HTTP errors (400, 413, 429, 500) from post-decryption encrypted envelopes (HTTP 200 with an `error` field) and handles both, including envelopes detected while polling `/execution/{id}/output`

### Requirement: Output Polling and Output-Integrity Attestation

The caller SHALL poll for execution output and validate a final output-integrity
attestation, tolerating server-side attestation rate limiting.

#### Scenario: Polling does not request intermediate attestations

- **WHEN** the caller polls `/execution/{id}/output` to track progress
- **THEN** it logs incremental stdout/stderr but does not request, validate, or store output attestation documents during intermediate polling

#### Scenario: Final output-integrity attestation is validated

- **WHEN** the poll response indicates completion (`complete: true`) and contains an `output_attestation_document`
- **THEN** the caller validates it, parsing the `user_data` JSON for `output_digest` (`{"output_digest": "<hex>", "execution_id": "<uuid>"}`) and comparing it to the locally computed digest, falling back to treating the raw `user_data` string as the hex digest if it is not valid JSON or lacks `output_digest`

#### Scenario: Attestation rate limiting is non-fatal

- **WHEN** the final response sets `output_attestation_document: null` with `attestation_rate_limited: true`
- **THEN** the caller treats it as a legitimate non-error condition, logs an informational message, and continues without failing
