# attested-executor-caller Design

Rationale behind [[attested-executor-caller]]. Distilled from the Kiro
`rust-attestated-build` design and requirements.

## Context

The caller (`call_remote_executor`) is the client for the Remote Executor's
attested channel: health check, OIDC token acquisition, NitroTPM attestation
validation, PQ-hybrid KEM key exchange, encrypted execution submission, output
polling, and output-integrity verification. It is reused from the
`github-runner-ec2-attestation-caller` project. Its job is not just to *run* the
remote build but to *prove* the execution and its output were not tampered with —
the trust that the rest of the pipeline ([[attested-build-workflow]]) builds on.

## Goals / Non-Goals

**Goals:**

- Forward exactly the environment the build needs into the enclave.
- Detect any server-side tampering of execution parameters or output.
- Surface server-side errors clearly, including those returned encrypted.
- Tolerate legitimate server behaviors (attestation rate limiting) without
  failing.

**Non-Goals:**

- Implementing the executor/server side — a separate repo.
- Building or transporting the binary — [[hardened-build-environment]] /
  [[attested-build-workflow]].

## Decisions

### Copy the caller module rather than submodule it

The module is vendored into `.github/scripts/call_remote_executor/` instead of
referenced as a git submodule. This keeps the demo self-contained and avoids
cross-repository dependency management, at the cost of having to keep verification
logic (e.g. `script_env_hash`) in sync with the upstream caller project.

### Verify the execution-acceptance attestation binds to *our* request

The caller recomputes the SHA-256 of the canonicalized `script_env` it sent — keys
sorted lexicographically, compact JSON separators `(',', ':')`, no whitespace, `{}`
when empty — and checks it against the attested `script_env_hash`, and checks the
attested `execution_id` against the response body. This detects a server that
silently injected or altered environment variables or swapped executions: the
attestation must bind to the exact parameters the caller chose, not just to *some*
valid execution. Canonicalization must match the server's algorithm byte-for-byte
or the hashes won't compare equal.

### Treat encrypted error envelopes as errors, not successes

After a successful decryption the server can return HTTP 200 whose payload contains
an `error`/`error_code` — an encrypted error envelope, returned with a 200 at the
transport layer so observers can't distinguish errors from successes. The caller
inspects decrypted payloads (on `/execute` and while polling `/execution/{id}/output`)
and raises a `CallerError` from the enclosed details rather than misreading the
envelope as a successful execution. Pre-decryption plaintext errors (400/413/429/500)
are handled separately from these post-decryption envelopes.

### Obtain the output-integrity attestation only on the final poll

Intermediate polls only stream stdout/stderr for progress; the caller does **not**
request, validate, or store output attestations during them. Output integrity is
validated once, on the completing poll (`complete: true`). This minimizes load on
the server's NitroTPM attestation path while still proving the final output's
integrity.

### Tolerate output-attestation rate limiting as non-fatal

If the final response sets `output_attestation_document: null` with
`attestation_rate_limited: true`, the caller logs an informational message and
continues. This is a legitimate server defense against turning frequent polling
into a TPM resource-exhaustion path — the output itself is still returned — so it
must not be treated as a verification failure.

### Robust `output_digest` extraction with a legacy fallback

The output-integrity `user_data` is parsed as JSON
(`{"output_digest": "<hex>", "execution_id": "<uuid>"}`) and `output_digest` is
compared to the locally computed digest. If `user_data` is not valid JSON or lacks
`output_digest`, the caller falls back to treating the whole string as the raw hex
digest, keeping compatibility with older servers.

## Risks / Trade-offs

- **Vendored drift.** Copying the caller means `script_env_hash` and envelope
  logic must be kept in sync with the upstream project by hand; the alternative
  (submodule) was rejected for self-containment.
- **Canonicalization coupling.** The `script_env_hash` check is only as good as the
  exact-match canonicalization; any divergence from the server's algorithm yields
  false mismatches.
- **Rate-limit tolerance widens the trust window.** Accepting a null output
  attestation under rate limiting means a completed run can lack a fresh
  output-integrity proof; accepted as the documented, legitimate server behavior
  that protects the TPM.
