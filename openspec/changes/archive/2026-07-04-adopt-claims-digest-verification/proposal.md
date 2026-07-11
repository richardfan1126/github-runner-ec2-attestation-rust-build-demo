## Why

The Remote Executor server changed its attestation wire format in a **BREAKING**
way (`attestation-claims-digest`, archived in the server repo on 2026-07-02). The
variable-length claim fields that used to sit **inline** in the attestation's
`user_data` were moved behind a fixed-size digest: `user_data` is now a compact
signed envelope `{ v, claims_digest, timestamp, execution_id }`, and the claim
fields travel alongside the attestation as a base64 opaque blob `claims_raw`
carried in the response body, bound by `sha256(decode(claims_raw)) == claims_digest`.
The output-integrity digest also changed algorithm — from the delimiter-glued
string `stdout:{o}\nstderr:{e}\nexit_code:{c}` to a canonical JSON object
`{ stdout, stderr, exit_code }` (`sort_keys`, `(',',':')`, `sha256:`-prefixed) — to
close a serialization-collision hazard.

That server change explicitly deferred the consumer half to this repo (its task
8.4, left unchecked: *"Update the `github-runner-ec2-attestation-rust-build-demo`
caller / bundled verifier to the recompute-and-compare flow"*). Until it lands,
**this repo's caller is incompatible with the server it talks to**:

- `caller.py::execute()` reads `repository_url` / `commit_hash` / `script_path` /
  `script_env_hash` / `execution_id` **directly from `user_data`**. Under the new
  format those fields are no longer in `user_data`. The non-None-guarded loop over
  `repository_url` / `commit_hash` / `script_path` hard-fails (`None != sent`),
  killing the execution flow; the None-guarded `script_env_hash` and `execution_id`
  checks would instead **silently become no-ops** — binding verification vanishing
  rather than failing loud is the more dangerous outcome.
- `attestation.py::validate_output_attestation()` reads `output_digest` from
  `user_data` and recomputes the old glued-string digest → mismatch → hard failure.
- The whole **recompute-and-compare** contract (decode `claims_raw` → hash →
  compare `claims_digest` → gate on envelope `v` / claims `schema_version` → fail
  closed if the preimage is missing) does not exist here yet.

## What Changes

### Two distinct binding layers

The verification splits into two checks that answer different questions with
different inputs, and this change keeps them in different places:

- **Integrity binding — "has the claims document been tampered with?"** Extract
  `claims_digest` from the *signed* `user_data` envelope, base64-decode `claims_raw`,
  `sha256` the decoded bytes, and compare. This is pure tamper-evidence: it needs
  **no caller state**, only the attestation and the sidecar bytes, and it lives in
  `attestation.py` next to signature verification (it is the same category of check).
  It proves the claims are *authentic* — exactly what the TPM signed — not that
  their content is honest.
- **Request binding — "did the server run *my* request?"** Compare the attested
  `repository_url` / `commit_hash` / `script_path` / `script_env_hash` against what
  the caller actually sent (or recomputed), and `execution_id` against the response
  body. This needs **caller state** and stays in `caller.py`. It is what rejects a
  perfectly-signed, untampered claims document that a malicious *server* authored for
  a different repository.

Concretely:

- **Adopt the claims-digest integrity binding.** Both the `/execute` and final
  `/execution/{id}/output` responses now carry a base64 `claims_raw` alongside the
  attestation. The caller MUST run the integrity binding **before reading any claim
  field**, reject an unknown digest-algorithm prefix, and **fail closed**: missing
  `claims_raw` or a digest mismatch ⇒ read no fields and reject (no
  trusted-but-empty read).
- **Read claim fields from `claims_raw`, not `user_data`.** `execute()`'s request
  binding reads `repository_url` / `commit_hash` / `script_path` / `script_env_hash`
  from the verified claims document; `execution_id` is read from the **envelope** (it
  stays inline and signed).
- **Binding fields are mandatory — remove the silent-skip.** Within a claims
  `schema_version` MAJOR the caller accepts, every field it can independently produce
  (sent values, or recomputed hashes like `script_env_hash` / `output_digest`) is
  guaranteed present. So an *absent* binding field is not legitimate evolution — it
  is tampering — and MUST fail closed. This is sound precisely because the version
  gate below rejects the only legitimate way a field can disappear (a MAJOR bump):
  fail-closed here has **zero false-reject surface**. The current None-guards on
  `script_env_hash` / `execution_id` are removed.
- **Version gating (per the server design's D10).** Reject an unknown **envelope
  `v`** before running the binding. Reject an unknown claims **`schema_version`
  MAJOR**; tolerate a higher **MINOR** and **ignore unknown claim fields**, so future
  additive server claims do not force another coordinated caller update. MINOR
  tolerance needs no new code — the caller only reads fields it knows, so unknown
  keys are ignored by construction; the only active checks are the three rejects
  (unknown `v`, unknown MAJOR, unknown algorithm prefix).
- **Update output-integrity verification.** The output validator reads `output_digest`
  from the verified claims document and recomputes it over the canonical JSON object
  `{ stdout, stderr, exit_code }` (`sort_keys=True`, `separators=(',',':')`,
  `exit_code` a JSON number, `sha256:`-prefixed), replacing the delimiter-glued
  reconstruction. The saved artifact digest (`poll_output` → `artifact.py`) is updated
  to the same canonical form so stored provenance matches what was verified.
- **Tolerate the new `gpu` claim block.** It is additive and safely ignorable under
  the ignore-unknown-fields rule; the caller MAY log it but is not required to act on
  it.

### `/attest` is explicitly carved out (do not overload the shared verifier)

`validate_attestation` is shared by two call sites: `/attest` (server identity) and
`/execute`. **`/attest` carries no `claims_raw`** — its response is only
`{ attestation_document, server_public_key }`, and the caller reads the key
fingerprint from the attestation's native `public_key` field, never from claims. So
the integrity binding MUST NOT be baked into `validate_attestation` — doing so would
fail-closed on `/attest` and break server-identity attestation, the first step of the
whole flow.

Instead, the `/execute` path gets a **new, phase-specific execution validator** that
composes COSE verification + integrity binding + version gate and returns the trusted
claims; `validate_attestation` stays a bare COSE-verify used unchanged by `/attest`;
`validate_output_attestation` gains the `claims_raw` parameter and the binding. The
integrity binding is thus never an optional argument a call site can forget — it is
inside the only functions that return claims, so it cannot be half-invoked.

### Migration hazard (call out for the implementer)

A naive "make the errors go away" migration produces a **green but silently insecure**
result. The `repository_url` / `commit_hash` / `script_path` reads fail *loud* (they
raise) and get fixed; `execution_id` keeps working *by luck* (it stayed in the
envelope), masking a sense of completion; but `script_env_hash` — the only field that
both moved to `claims_raw` **and** is None-guarded — silently evaporates, dropping the
binding on the environment forwarded into the build. The fix must consciously
*relocate* `script_env_hash` to the claims read and make its absence fail closed, not
just chase the raises.

This is a consumer-side wire-format adoption only. The attested-channel handshake,
HPKE encryption, PCR/nonce/certificate-chain verification, OIDC flow, and the workflow
orchestration are unchanged.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `attested-executor-caller`: The "Execution-Acceptance Attestation Verification"
  requirement changes — claim fields are read from the digest-bound `claims_raw`
  preimage (not inline `user_data`), gated by a mandatory integrity-binding check
  (decode → hash → compare, fail-closed) and by envelope-`v` / `schema_version`-MAJOR
  rejection with MINOR tolerance and unknown-field ignoring; binding fields are
  mandatory (no silent-skip); `execution_id` is read from the signed envelope. The
  "Output Polling and Output-Integrity Attestation" requirement changes — the final
  output attestation's `output_digest` is read from `claims_raw` and recomputed over
  canonical JSON `{ stdout, stderr, exit_code }` rather than the glued string. A new
  requirement captures the two-layer binding model (integrity vs request), the
  fail-closed contract, the version-gate policy, and the `/attest` carve-out (server
  identity verification is not subjected to the claims binding).

## Impact

- **Files changed:** `.github/scripts/call_remote_executor/attestation.py` (bare
  `validate_attestation` preserved for `/attest`; **new** execution-attestation
  validator; `validate_output_attestation` gains `claims_raw`; shared
  integrity-binding + version-gate helper), `.github/scripts/call_remote_executor/caller.py`
  (`execute()` request binding reads from claims, None-guards removed; `poll_output()`
  artifact digest), `.github/scripts/call_remote_executor/artifact.py` (stored
  `output_digest` canonical form); `openspec/specs/attested-executor-caller/spec.md`
  (scenarios at lines 40, 45, 76 describe the retired inline/glued contract). Test
  suite under `tests/`.
- **Required regression tests (each pins a specific hazard surfaced in exploration):**
  (A) a *wrong* `script_env_hash` in `claims_raw` is rejected; (B) an *absent*
  `script_env_hash` in `claims_raw` is rejected (the fail-closed teeth — a tamper test
  alone cannot catch a surviving None-guard); (C) an `/attest` attestation carrying no
  `claims_raw` still validates and is **not** subjected to the binding.
- **BREAKING against the old server:** after this change the caller expects
  `claims_raw` and the envelope `user_data`; it will no longer parse the retired
  inline-`user_data` format. Coordinated cutover with the already-shipped server
  change — no dual-format support, gated on `schema_version`.
- **Security posture preserved and hardened:** every field the caller trusts stays
  covered by the TPM signature (directly via the envelope, or via `claims_digest` over
  `claims_raw`); the previously silent-skippable `script_env_hash`/`execution_id`
  checks become fail-closed.
- **Not impacted:** the encrypted channel (`claims_raw` rides inside the existing
  sealed response body), PCR/nonce/cert-chain checks, the OIDC token flow, the
  `/attest` server-identity path, and the `attested-build-workflow` orchestration.
