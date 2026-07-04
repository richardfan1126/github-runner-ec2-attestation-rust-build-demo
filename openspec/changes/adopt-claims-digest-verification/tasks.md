## 1. Version pins and shared binding helper (`attestation.py`)

- [ ] 1.1 Add module-level scalar constants next to `MAX_ATTESTATION_B64_SIZE`: `ACCEPTED_ENVELOPE_VERSION = 1`, `ACCEPTED_CLAIMS_SCHEMA_MAJOR = 1`, `SHA256_DIGEST_PREFIX = "sha256:"`, with a comment naming the server constants they mirror (`ENVELOPE_VERSION`, `CLAIMS_SCHEMA_VERSION`) and the one-line-bump / no-dual-format rationale (design D5, resolved OQ)
- [ ] 1.2 Add `_verify_claims_binding(payload_doc, claims_raw) -> dict` doing **integrity + version gate ONLY** (design D3/D5): read the `{ v, claims_digest, timestamp, execution_id }` envelope from the trusted `user_data`; reject `v != ACCEPTED_ENVELOPE_VERSION`; base64-decode `claims_raw`; verify `claims_digest` carries the `SHA256_DIGEST_PREFIX` and `sha256(decoded) == claims_digest`; parse claims JSON; reject `schema_version` MAJOR `!= ACCEPTED_CLAIMS_SCHEMA_MAJOR`; return the parsed claims dict. Do **not** check field presence here (that is per-phase, D4)
- [ ] 1.3 Make every failure path in 1.2 raise `CallerError` (fail closed): absent/empty `claims_raw`, bad base64, unknown `v`, unknown algorithm prefix, digest mismatch, malformed/absent `schema_version`, unknown MAJOR — each with a phase-tagged message, and never a silent skip
- [ ] 1.4 Confirm ordering: `_verify_claims_binding` is only ever called **after** COSE signature/PKI/PCR/nonce verification has produced a trusted `payload_doc` (design D2 hard constraint)

## 2. Execution-attestation validator (`attestation.py`)

- [ ] 2.1 Add `validate_execution_attestation(att_b64, claims_raw, ...) -> (payload_doc, claims)` that **composes** the existing bare `validate_attestation(att_b64, ...)` for COSE verification (no new/duplicated COSE code — design D3), then calls `_verify_claims_binding` and returns the trusted claims
- [ ] 2.2 In `validate_execution_attestation`, enforce the **execution-phase mandatory set** (D4/D6): `repository_url`, `commit_hash`, `script_path`, `script_env_hash` MUST be present in the claims; a missing one raises `CallerError` (fail closed). Do not enforce output-phase fields here
- [ ] 2.3 Leave `validate_attestation` unchanged — it stays the bare COSE verifier used by `/attest`, carries no claims binding (design D3 carve-out)

## 3. Output-attestation validator (`attestation.py`)

- [ ] 3.1 Add the `claims_raw` parameter to `validate_output_attestation(att_b64, claims_raw, stdout, stderr, exit_code, ...)`; keep its own inline COSE steps (pre-existing duplication left as-is per Non-Goal), then call `_verify_claims_binding`
- [ ] 3.2 Enforce the **output-phase mandatory set** (D6, resolved OQ1): require **only** `output_digest`; MUST NOT require the four execution fields — they are legitimately absent from output claims, and requiring them would false-reject every output attestation
- [ ] 3.3 Recompute `output_digest` over canonical JSON `json.dumps({"stdout":..., "stderr":..., "exit_code":...}, sort_keys=True, separators=(',',':'))` with `exit_code` a JSON number, `SHA256_DIGEST_PREFIX`-prefixed; compare to the attested `output_digest` (design D7). Remove the retired glued-string `stdout:...\nstderr:...\nexit_code:...` reconstruction and the raw-`user_data` fallback

## 4. Caller request binding (`caller.py`)

- [ ] 4.1 At the `/execute` call site, switch from `validate_attestation` to `validate_execution_attestation`, extracting `claims_raw` from the decrypted `/execute` response body (sibling of `attestation_document`) and threading it in
- [ ] 4.2 Rewrite the request binding to read `repository_url`, `commit_hash`, `script_path`, `script_env_hash` from the **verified claims dict** (not `user_data`), comparing each to the sent/recomputed value and raising `CallerError` naming expected vs attested on mismatch
- [ ] 4.3 **Remove the `if attested is not None:` None-guards** on `script_env_hash` and `execution_id` (design D4) — absence is now tampering and must fail closed. Do **not** reintroduce optionality just because the server omits `claims_raw` for its own test doubles (design Risk: fail-OPEN landmine)
- [ ] 4.4 Read `execution_id` from the signed **envelope** (it stays inline) and verify it matches the `execution_id` in the decrypted `/execute` response body
- [ ] 4.5 Leave the `/attest` call site on the bare `validate_attestation`; confirm it still reads the key fingerprint from the attestation's native `public_key` field and is never handed `claims_raw`

## 5. Output polling and stored provenance (`caller.py`, `artifact.py`)

- [ ] 5.1 At the final `/output` poll site, extract `claims_raw` from the response body and pass it into `validate_output_attestation`
- [ ] 5.2 Update the artifact/provenance digest saved in `poll_output` (~`caller.py`) / `artifact.py` to the identical canonical `SHA256_DIGEST_PREFIX`-prefixed JSON form used in 3.3, so stored provenance ≡ what was verified (design D7); import `SHA256_DIGEST_PREFIX` rather than re-literaling it

## 6. Regression tests

- [ ] 6.1 Build a positively-constructed execution `claims_raw` fixture helper: real JSON claims → base64 → recompute `claims_digest` → embed in a freshly signed `user_data` envelope, so tests exercise the **presence/binding** layers, not the integrity layer (design: a claims-less server double is rejected one layer too early and proves the wrong thing)
- [ ] 6.2 **Test A** — a *wrong* `script_env_hash` in a well-formed, correctly-bound `claims_raw` is rejected by `validate_execution_attestation` + request binding
- [ ] 6.3 **Test B** — an *absent* `script_env_hash` in an otherwise well-formed, correctly-bound `claims_raw` is rejected (the fail-closed teeth for the removed None-guard; a tamper test alone cannot catch a surviving guard)
- [ ] 6.4 **Test C** — an `/attest` attestation carrying **no** `claims_raw` still validates via `validate_attestation` and is **not** subjected to the claims binding
- [ ] 6.5 Add a missing-`claims_raw`-on-`/execute` test: absence is rejected (fail closed) regardless of stated cause, confirming the server's conditional omission is not mirrored as caller optionality
- [ ] 6.6 Add version-gate tests: unknown envelope `v`, unknown `schema_version` MAJOR, and unknown digest algorithm prefix are each rejected; a higher MINOR and an unknown additive field (e.g. a `gpu` block) are tolerated
- [ ] 6.7 Add an output-digest test: `validate_output_attestation` accepts the canonical-JSON `output_digest` and rejects a mismatch; assert only `output_digest` is required (no execution-field false-reject)

## 7. Spec sync and verification

- [ ] 7.1 Update `openspec/specs/attested-executor-caller/spec.md` to the new contract (the retired scenarios at the old inline-`user_data` / glued-digest lines), consistent with the delta under this change
- [ ] 7.2 Run the caller test suite; confirm A/B/C and the version-gate/output tests pass and no pre-existing test regresses
- [ ] 7.3 `openspec validate adopt-claims-digest-verification` passes; run `openspec archive` only after implementation and review
