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

- **Adopt the claims-digest binding.** Both the `/execute` and final
  `/execution/{id}/output` responses now carry a base64 `claims_raw` alongside the
  attestation. The caller MUST base64-decode it, compute `sha256` over the decoded
  bytes, and compare against the `sha256:`-prefixed `claims_digest` in the signed
  `user_data` envelope **before reading any claim field**. Reject an unknown digest
  algorithm prefix. **Fail closed:** missing `claims_raw` or a digest mismatch ⇒
  read no fields and reject (no trusted-but-empty read).
- **Read claim fields from `claims_raw`, not `user_data`.** `execute()`'s request
  binding (`repository_url`, `commit_hash`, `script_path`, `script_env_hash`) reads
  from the verified claims document; `execution_id` is read from the **envelope**
  (it stays inline and signed). Remove the silent-skip so a claim that is expected
  but absent fails closed instead of passing.
- **Version gating (per the server design's D10).** Reject an unknown **envelope
  `v`** before running the binding. Reject an unknown claims **`schema_version`
  MAJOR**; tolerate a higher **MINOR** and **ignore unknown claim fields**, so
  future additive server claims do not force another coordinated caller update.
- **Update output-integrity verification.** `validate_output_attestation()` reads
  `output_digest` from the verified claims document and recomputes it over the
  canonical JSON object `{ stdout, stderr, exit_code }` (`sort_keys=True`,
  `separators=(',',':')`, `exit_code` a JSON number, `sha256:`-prefixed), replacing
  the delimiter-glued reconstruction. The saved artifact digest
  (`poll_output` → `artifact.py`) is updated to the same canonical form so stored
  provenance matches what was verified.
- **Tolerate the new `gpu` claim block.** It is additive and safely ignorable under
  the ignore-unknown-fields rule; the caller MAY log it but is not required to act
  on it.

This is a consumer-side wire-format adoption only. The attested-channel handshake,
HPKE encryption, PCR/nonce/certificate-chain verification, OIDC flow, and the
workflow orchestration are unchanged.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `attested-executor-caller`: The "Execution-Acceptance Attestation Verification"
  requirement changes — claim fields are read from the digest-bound `claims_raw`
  preimage (not inline `user_data`), gated by a mandatory recompute-and-compare
  binding check and by envelope-`v` / `schema_version`-MAJOR version rejection with
  MINOR tolerance and unknown-field ignoring; `execution_id` is read from the signed
  envelope. The "Output Polling and Output-Integrity Attestation" requirement changes
  — the final output attestation's `output_digest` is read from `claims_raw` and
  recomputed over canonical JSON `{ stdout, stderr, exit_code }` rather than the
  glued string. A new binding/fail-closed requirement captures the recompute step,
  the fail-closed contract, and the version-gate policy.

## Impact

- **Files changed:** `.github/scripts/call_remote_executor/attestation.py`
  (`validate_attestation`, `validate_output_attestation`, new claims-binding + version
  gate), `.github/scripts/call_remote_executor/caller.py` (`execute()` request
  binding, `poll_output()` artifact digest), possibly
  `.github/scripts/call_remote_executor/artifact.py` (stored `output_digest` form);
  `openspec/specs/attested-executor-caller/spec.md` (scenarios at lines 40, 45, 76
  describe the retired inline/glued contract). Test suite under `tests/` for the new
  binding and canonical-JSON digest paths.
- **BREAKING against the old server:** after this change the caller expects
  `claims_raw` and the envelope `user_data`; it will no longer parse the retired
  inline-`user_data` format. This is a coordinated cutover with the already-shipped
  server change — no dual-format support, gated on `schema_version`.
- **Security posture preserved and hardened:** every field the caller trusts stays
  covered by the TPM signature (directly via the envelope, or via `claims_digest`
  over `claims_raw`); the previously silent-skippable `script_env_hash`/`execution_id`
  checks become fail-closed.
- **Not impacted:** the encrypted channel (`claims_raw` rides inside the existing
  sealed response body), PCR/nonce/cert-chain checks, the OIDC token flow, and the
  `attested-build-workflow` orchestration.
