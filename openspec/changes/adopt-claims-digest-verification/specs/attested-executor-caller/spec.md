## ADDED Requirements

### Requirement: Claims-Digest Integrity Binding and Version Gate

The caller SHALL verify the integrity binding of the sidecar `claims_raw` blob
against the signed `claims_digest` before reading any claim field, gate on the
envelope and claims versions, and fail closed on any binding or version failure.
This integrity binding is a distinct layer from the request binding: it proves the
claims document is **authentic** (exactly what the TPM signed), not that its content
is **honest** — the latter is the province of the request-binding checks under
"Execution-Acceptance Attestation Verification". The integrity binding needs no
caller state (only the attestation and the `claims_raw` bytes) and therefore lives
with signature verification, strictly downstream of it.

#### Scenario: Integrity binding runs after signature verification and before any claim read

- **WHEN** an `/execute` or final `/execution/{id}/output` response carries a base64
  `claims_raw` alongside the attestation
- **THEN** the caller first completes COSE signature / certificate-chain / PCR / nonce
  verification, then extracts `claims_digest` from the *signed* `user_data` envelope,
  base64-decodes `claims_raw`, computes its SHA-256, and rejects unless
  `sha256(decode(claims_raw)) == claims_digest` — reading no claim field until this
  comparison passes

#### Scenario: Missing preimage or digest mismatch fails closed

- **WHEN** `claims_raw` is absent from a response that requires it, or the recomputed
  SHA-256 does not equal the signed `claims_digest`
- **THEN** the caller reads no claim field and raises an error (no trusted-but-empty
  read), rather than proceeding with unbound or partially-read claims

#### Scenario: Unknown envelope version, claims MAJOR, or digest algorithm is rejected

- **WHEN** the envelope `v` is not the accepted value (`1`), or the claims
  `schema_version` MAJOR is not the accepted value (`1`), or a digest carries an
  algorithm prefix other than `sha256:`
- **THEN** the caller rejects the attestation before running the binding or reading
  fields, because it cannot safely locate the binding mechanism, interpret the fields,
  or check the digest

#### Scenario: Higher MINOR and unknown claim fields are tolerated

- **WHEN** the claims `schema_version` shares the accepted MAJOR but carries a higher
  MINOR, or the claims document contains additive fields (such as a `gpu` block) the
  caller does not recognise
- **THEN** the caller accepts the attestation and ignores the unrecognised fields,
  reading only the fields it knows, so additive server evolution does not force a
  coordinated caller update

#### Scenario: /attest server-identity attestation is not subjected to the claims binding

- **WHEN** the caller verifies the `/attest` server-identity attestation, whose
  response is `{ attestation_document, server_public_key }` and carries no `claims_raw`
- **THEN** the caller validates it with the bare COSE verifier and reads the key
  fingerprint from the attestation's native `public_key` field, and does NOT apply the
  claims integrity binding (which would fail closed on the absent `claims_raw` and
  break server-identity verification)

## MODIFIED Requirements

### Requirement: Execution-Acceptance Attestation Verification

The caller SHALL verify that the execution-acceptance attestation binds to the
exact request it sent, detecting server-side tampering of the execution
parameters. It SHALL read the request-binding claim fields from the integrity-bound
`claims_raw` preimage (not inline `user_data`), only after the integrity binding and
version gate (see "Claims-Digest Integrity Binding and Version Gate") pass. Within an
accepted claims `schema_version` MAJOR every request-binding field is mandatory: an
absent binding field is tampering and MUST fail closed (no silent skip).

#### Scenario: Request-binding fields are read from the verified claims document

- **WHEN** the execution-acceptance attestation and its `claims_raw` have passed the
  integrity binding and version gate
- **THEN** the caller reads `repository_url`, `commit_hash`, `script_path`, and
  `script_env_hash` from the verified claims document (not from `user_data`) and
  verifies each against the value it sent or recomputed, raising a CallerError naming
  the expected and attested values on any mismatch

#### Scenario: script_env_hash is verified

- **WHEN** the execution-acceptance attestation's verified claims document contains a `script_env_hash`
- **THEN** the caller computes the SHA-256 of the canonicalized `script_env` it sent (keys sorted lexicographically, compact JSON separators `(',', ':')`, no whitespace; `{}` when empty), and verifies the attested hash matches — raising a CallerError naming both expected and attested values on mismatch

#### Scenario: An absent request-binding field fails closed

- **WHEN** a request-binding field mandatory for the execution phase (`repository_url`,
  `commit_hash`, `script_path`, or `script_env_hash`) is absent from the verified
  claims document
- **THEN** the caller raises a CallerError rather than skipping the check, because
  within an accepted MAJOR the field is guaranteed present and its absence is tampering
  (the version gate rejects the only legitimate way a field could disappear, so
  fail-closed here has no false-reject surface)

#### Scenario: execution_id is verified

- **WHEN** the execution-acceptance attestation's signed `user_data` envelope contains an `execution_id`
- **THEN** the caller verifies it matches the `execution_id` in the decrypted `/execute` response body, raising a CallerError describing the mismatch otherwise

### Requirement: Output Polling and Output-Integrity Attestation

The caller SHALL poll for execution output and validate a final output-integrity
attestation, tolerating server-side attestation rate limiting.

#### Scenario: Polling does not request intermediate attestations

- **WHEN** the caller polls `/execution/{id}/output` to track progress
- **THEN** it logs incremental stdout/stderr but does not request, validate, or store output attestation documents during intermediate polling

#### Scenario: Final output-integrity attestation is validated

- **WHEN** the poll response indicates completion (`complete: true`) and contains an `output_attestation_document` with a sidecar `claims_raw`
- **THEN** the caller validates the attestation, runs the claims integrity binding and version gate, reads `output_digest` from the verified claims document, and compares it to the digest it recomputes over the canonical JSON object `{ stdout, stderr, exit_code }` (`json.dumps(sort_keys=True, separators=(',', ':'))`, `exit_code` a JSON number, `sha256:`-prefixed) — replacing the retired delimiter-glued `stdout:…\nstderr:…\nexit_code:…` reconstruction and its raw-`user_data` fallback

#### Scenario: Output claims require only output_digest

- **WHEN** the caller verifies the output-integrity attestation
- **THEN** it requires only `output_digest` (plus `execution_id` from the signed envelope) in the verified claims document, and MUST NOT require the execution-phase fields (`repository_url`, `commit_hash`, `script_path`, `script_env_hash`), which are legitimately absent from output claims — a global "all binding fields present" check would false-reject every output attestation

#### Scenario: Saved artifact digest matches the verified canonical form

- **WHEN** the caller records the output digest as provenance (`poll_output` → artifact collector)
- **THEN** it stores the same canonical JSON `sha256:`-prefixed digest it verified, so stored provenance matches what was attested rather than the retired glued-string form

#### Scenario: Attestation rate limiting is non-fatal

- **WHEN** the final response sets `output_attestation_document: null` with `attestation_rate_limited: true`
- **THEN** the caller treats it as a legitimate non-error condition, logs an informational message, and continues without failing
