## Context

The Remote Executor server has already shipped a BREAKING attestation wire-format
change (`attestation-claims-digest`). The claim fields that used to sit inline in the
NitroTPM attestation's `user_data` now live in a base64 opaque blob `claims_raw`
carried alongside the attestation in the response body, bound by a digest inside a
compact signed envelope. This change updates **the caller/verifier in this repo**
(`.github/scripts/call_remote_executor/`) to consume that format. See `proposal.md`
for motivation and the incompatibility inventory.

Concrete wire facts this design targets (verified against the server source):

- Envelope in `user_data`: `{ v, claims_digest, timestamp, execution_id }`, with
  `ENVELOPE_VERSION = 1`.
- Claims document (`claims_raw`) top-level `schema_version = "1.0"` (`MAJOR.MINOR`).
- Digests are `sha256:<hex>` — `claims_digest` over the decoded `claims_raw` bytes,
  and the inner `output_digest` over canonical JSON `{ stdout, stderr, exit_code }`.
- `/execute` and `/execution/{id}/output` responses carry `claims_raw`; **`/attest`
  does not** — its response is `{ attestation_document, server_public_key }` and the
  key fingerprint is read from the attestation's native `public_key` field.

The three call sites of the shared verifier today:

```
  caller.py:340  /attest   → validate_attestation(att)   → reads payload["public_key"]   (NO claims_raw)
  caller.py:510  /execute  → validate_attestation(att)   → reads user_data + binds fields (HAS claims_raw)
  caller.py:759  /output   → validate_output_attestation(att, stdout, stderr, exit_code) (HAS claims_raw)
```

## Goals / Non-Goals

**Goals:**
- Consume the claims-digest format: verify the integrity binding of `claims_raw`
  against the signed `claims_digest` before reading any claim field, and read the
  request-binding fields from the verified claims document.
- Preserve every security guarantee the caller has today, and *harden* the two
  checks (`script_env_hash`, `execution_id`) that are currently silently skippable.
- Keep the verifier forward-compatible: additive server claims (MINOR) must not force
  another coordinated caller update.
- Do not break the `/attest` server-identity path, which shares the verifier but
  carries no claims.

**Non-Goals:**
- No dual-format support. This is a clean cutover coordinated with the already-shipped
  server change, gated on `schema_version`.
- No change to the attested-channel handshake, HPKE encryption, PCR/nonce/cert-chain
  verification, OIDC flow, or the `attested-build-workflow` orchestration.
- No consumption of the new `gpu` block beyond tolerating it (ignore-unknown-fields).
- Not fixing the pre-existing ~80-line duplication between the two verifier functions
  (see Risks) — only ensuring the *new* logic is not duplicated.

## Decisions

### D1 — Two binding layers, in two different places

Verification splits into two checks that answer different questions with different
inputs, and each defeats an adversary the other structurally cannot see:

```
  INTEGRITY binding   "has claims_raw been tampered with?"    catches a WIRE TAMPERER
    inputs: (attestation, claims_raw)     ── no caller state ──   lives in attestation.py
    claims_digest (from signed user_data)  ==  sha256(decode(claims_raw))

  REQUEST binding     "did the server run MY request?"         catches a LYING SERVER
    inputs: (trusted claims, what-I-sent) ── caller state  ──   lives in caller.py
    attested repository_url/commit_hash/script_path/script_env_hash == sent/recomputed
```

Integrity passing means the claims are *authentic* (exactly what the TPM signed), not
that their content is *honest*. A malicious server can author a perfectly-signed,
untampered claims doc naming `evil/repo`; integrity passes, and only the request
binding — which needs caller state `attestation.py` must not hold — rejects it.

**Alternative rejected:** a single combined check. It would force `attestation.py` to
know caller state, coupling the tamper-evidence layer to request specifics and making
the `/attest` path (no request to bind) awkward.

### D2 — Integrity binding lives with signature verification, strictly downstream of it

The integrity binding is the same category of check as the COSE signature / PKI / PCR
/ nonce verification: authenticity, no caller state. It therefore lives in
`attestation.py`. It MUST run **after** signature verification — `claims_digest` is
only trustworthy once `user_data` is proven signed:

```
  1. COSE verify (sig / PKI / PCR / nonce)        ← establishes user_data authentic
  2. read claims_digest from the trusted envelope
  3. decode claims_raw → sha256 → compare          ← transitively trusts claims_raw
  4. version gate (D5)
  5. parse claims → return   (only now may caller.py read + request-bind)
```

Binding before step 1 compares against an unsigned digest — worthless. This ordering
is a hard constraint, not a convenience.

### D3 — Phase-specific validators; `/attest` carve-out; shared helpers

`validate_attestation` is shared by `/attest` (no claims) and `/execute` (has claims).
Baking a mandatory integrity binding into it would fail-closed on `/attest` and break
server-identity attestation. So the verifier surface becomes three phase-specific
functions over shared internals:

```
  validate_attestation(att_b64, …)                              → payload_doc
      pure COSE verify. Used by /attest. UNCHANGED, no claims binding.

  validate_execution_attestation(att_b64, claims_raw, …)        → (payload_doc, claims)   ◀ NEW
      validate_attestation(att) → integrity binding → version gate → presence check →
      return trusted claims. Used by /execute. COMPOSES the existing bare verifier; adds no COSE code.

  validate_output_attestation(att_b64, claims_raw, stdout, stderr, exit_code, …) → bool
      (its own inline COSE) → integrity binding → output_digest recompute. Used by /output. (gains claims_raw)

  shared internals:
      _verify_claims_binding(payload, raw) → claims           (integrity + version gate ONLY; D5)
      (no _verify_cose extraction — see below)
```

The shared `_verify_claims_binding` does the integrity binding and version gate and
**nothing else** — in particular it does **not** enforce which claim fields must be
present. Mandatory-field presence is *per-phase* (see D4) and therefore lives in each
phase-specific validator, not in the shared helper. Folding a "require these fields"
check into the shared helper would false-reject one phase or the other, since the two
phases carry disjoint field sets.

Only **one** new shared helper is introduced (`_verify_claims_binding`); there is
deliberately **no** `_verify_cose` extraction. `validate_execution_attestation` reuses
the existing bare `validate_attestation` as its COSE seam (it already returns
`payload_doc`), and `validate_output_attestation` keeps its own inline COSE steps. So
the pre-existing ~80-line COSE duplication is left exactly as-is (a Non-Goal), this
change adds **no** COSE code — only the binding — and there is no third copy of the
COSE steps to maintain. An earlier sketch of a shared `_verify_cose` would have meant
consolidating that duplication, contradicting the Non-Goal and widening scope; it is
dropped in favour of composing the verifier that already exists.

This matches the existing grain — `validate_output_attestation` is already a
phase-specific all-in-one — and makes the binding **impossible to forget**: it is
inside the only functions that return claims, never an optional argument a call site
can omit. The caller extracts `claims_raw` from the decrypted response body (a sibling
of `attestation_document`) and threads it in, keeping the verifier decoupled from the
response schema.

**The composition seam doubles as the test seam (Thread F).** Because
`_verify_claims_binding` consumes the *already-trusted* `payload_doc` that
`validate_attestation` returns post-COSE, the binding is unit-testable **without a
signing key or a test CA** — construct a `payload_doc` dict directly (its `user_data`
carries the envelope), pair it with a positively re-bound `claims_raw`, and call the
helper; or drive a phase validator with `validate_attestation` **patched** to return the
fixture doc when the presence/request-binding path is under test. This is the *same*
reason we composed rather than duplicated COSE: the seam that keeps this change to "no
new COSE code" is the seam the tests inject at. It extends the suite's existing
"patch the validator" convention (`tests/test_output_polling.py` patches
`validate_output_attestation` wholesale) one layer deeper — patch COSE, keep the binding
real. Note there is **no separate envelope signing key**: the `{v, claims_digest,
timestamp, execution_id}` envelope is plain JSON inside `user_data`, trusted only because
COSE covers the whole `payload_doc`; in a fixture, that trust is conferred by the patched
verifier, so "signed envelope" language must not imply a key the tests have to forge.
End-to-end COSE / PKI / PCR coverage is explicitly **out of scope** — that path is
unchanged by this change and is already untested-by-construction in this suite; adding a
real cert-chain fixture would be a separate, larger effort.

**Alternative rejected:** overload `validate_attestation` to always bind and return
`(payload_doc, claims)`. This was the initial interface sketch; it breaks `/attest`,
which has no `claims_raw`. Making `claims_raw` an *optional* parameter instead would
reintroduce the silent-skip hazard (a call site that forgets it binds nothing).

### D4 — Binding fields are mandatory within a known MAJOR; remove the None-guards

Today `script_env_hash` and `execution_id` are guarded by `if attested is not None:`
(a forward-compat hedge). Under the versioned format that hedge becomes a landmine —
an absent field silently skips its check. Decision: within a `schema_version` MAJOR
the caller accepts, every request-binding field is **mandatory**; an absent one is
tampering and MUST fail closed. The None-guards are removed.

This is **sound with zero false-reject surface**, and the proof is the version gate:

```
  How can a binding field legitimately disappear from the claims doc?
    removed / renamed / re-typed / re-meaned  → MAJOR bump → caller rejects → never reads
    additive optional field                   → MINOR bump → can only ADD, never remove
  ∴ within a MAJOR the caller reads, every binding field is present.
    absent there ⇒ not legitimate evolution ⇒ tampering ⇒ fail closed is safe.
```

Fail-closed is not a risk trade-off here; the version gate *earns* it. Without version
awareness, silent-skip was the only safe choice — with the gate, silent-skip is the
unsafe one.

**The mandatory set is per-phase, not global.** "Every binding field is mandatory"
means *every field mandatory for the phase being verified* — and the two phases carry
disjoint claim bodies (D6, resolved against the server source):

```
  execution claims → mandatory { repository_url, commit_hash, script_path, script_env_hash }
  output    claims → mandatory { output_digest }        ← does NOT contain the four exec fields
  (schema_version is "1.0" for BOTH — the version cannot tell you which set applies)
```

Because `schema_version` is shared across phases, a validator cannot use it to select
the mandatory set — the **phase is fixed by which validator you are in**. So the
presence check must live in the phase-specific validator (D3), never in the shared
binding helper. A global "all known binding fields must be present" check would
correctly pass execution attestations and **false-reject every output attestation**
(which legitimately lacks the four execution fields). This is the Thread-3 silent-vs-
loud trap one turn further: the tempting factoring — "presence is part of binding, and
D3 says share the binding helper" — is the bug.

### D5 — Version gate: three explicit rejects, MINOR tolerance is free

The gate rejects exactly three things and is otherwise permissive by construction:

```
  reject unknown envelope `v`            (≠ 1)          → cannot locate/trust the binding mechanism
  reject unknown claims MAJOR            (schema ≠ "1") → cannot safely interpret fields
  reject unknown digest algorithm prefix (≠ "sha256:") → cannot check the binding
```

Everything else is tolerated: a higher MINOR is accepted, and unknown claim fields are
ignored — **with no new code**, because the caller only reads fields it knows and
`dict.get()` ignores extra keys. So "tolerate MINOR / ignore unknown fields" needs no
allow-list and no MINOR-comparison logic beyond "is the MAJOR one I know."

**Alternative rejected:** strict reject-on-any-unknown-`schema_version`. That defeats
the server design's growth goal (additive claims would force lockstep caller upgrades),
and it is unnecessary because the integrity binding hashes transmitted bytes — adding a
field never breaks the binding, only interpretation, and only for a consumer that
chokes on unknown fields.

### D6 — The request-binding set is "what can I independently produce?", and it is phase-dependent

Membership of the request-binding set is generated by one test: *can the caller
independently produce this value?* — either it sent it, or it can recompute it. The
claim bodies below are the concrete shapes emitted by the server (verified against
`src/attestation.py`):

```
  execution claims_raw = { schema_version, repository_url, commit_hash, script_path,
                           script_env_hash, security{…}, gpu{…}? }
  output    claims_raw = { schema_version, output_digest, security{…}, gpu{…}? }

  execute phase  → bind { repository_url, commit_hash, script_path, script_env_hash }
                   observe { security, gpu }             (ignore, not caller-producible)
  output phase   → bind { output_digest }               (recomputed from stdout/stderr/exit_code)
                   observe { security, gpu }
  both phases    → bind execution_id  — from the ENVELOPE, not claims
```

This correctly classifies `output_digest` as a *bind* field (never sent, but
recomputable) which a naive "sent vs observed" framing would miss. Secrets
(`github_token`, `oidc_token`) are correctly *never* in claims — their absence is
required, not suspicious. The `security` posture block appears in **both** phases but
is *observe* in both — the caller does not send it (the server chooses the posture), so
it is not caller-producible and is not bound (see Open Questions for a future
minimum-posture policy hook).

### D7 — Output digest recomputed over canonical JSON; artifact digest matched

`validate_output_attestation` recomputes `output_digest` over the canonical JSON object
`{ stdout, stderr, exit_code }` (`json.dumps(sort_keys=True, separators=(',',':'))`,
`exit_code` a JSON number, `sha256:`-prefixed), replacing the delimiter-glued
`stdout:…\nstderr:…\nexit_code:…` string. The artifact-collector digest saved in
`poll_output` (`caller.py` ~766) is updated to the identical canonical form so stored
provenance matches what was verified. This mirrors the server's D11 and closes the same
in-band-delimiter collision hazard on the verify side.

The preimage is never transmitted: the server sends `stdout`, `stderr`, `exit_code` as
three separate decrypted JSON fields (`server.py` ~1147-1152), and only the resulting
`output_digest` rides inside the bound claims — so the caller cannot "hash the bytes it
received" as a single blob and MUST reconstruct the canonical object. Two rules keep that
reconstruction byte-exact: (1) the caller re-dumps the received `stdout`/`stderr`/`exit_code`
**verbatim** — treating them as opaque values, never parsing or normalising their content —
so that same-language (`json.dumps` on both sides) + same-params (pinned above) is
deterministically byte-identical (the cross-language canonicalisation-parity hazard does not
arise); and (2) it hashes the three fields taken **straight from the final `complete: true`
response body** — the server hashed the *full accumulated* `output_data.stdout`/`stderr`, so
the caller MUST NOT recompute from an accumulator it stitched across incremental polls (the
intermediate `stdout_offset`/`stderr_offset` chunks are for logging only and would hash to a
different, shorter preimage).

## Risks / Trade-offs

- **[Partial-migration silent regression on `script_env_hash`]** → A "make the errors
  go away" migration fixes the loud fields (`repository_url`/`commit_hash`/`script_path`
  raise) and leaves `script_env_hash` silently unverified (moved to `claims_raw` *and*
  None-guarded); `execution_id` keeps working by luck (stayed in the envelope), masking
  completion. → **Mitigation:** D4 removes the None-guard, and a required **strip-test**
  (absent `script_env_hash` ⇒ reject) gives fail-closed its teeth — a tamper-test alone
  cannot catch a surviving None-guard.
- **[Overloading the shared verifier breaks `/attest`]** → Adding binding to
  `validate_attestation` fails-closed on the claim-less server-identity path. →
  **Mitigation:** D3 carve-out + a required regression test that an `/attest`
  attestation with no `claims_raw` still validates and is not subjected to the binding.
- **[BREAKING cutover]** → The caller will no longer parse the retired inline format;
  it must talk to a server already emitting `schema_version` 1.x. → **Mitigation:**
  coordinated cutover (the server side is already shipped), gated on `schema_version`;
  rollback is a code revert on both sides.
- **[Pre-existing ~80-line duplication between the two verifier functions]** → The COSE
  size-check/decode/structural/cert/sig/PCR/nonce/logging steps are already copy-pasted,
  a drift hazard. → **Mitigation (bounded):** not refactored by this change to keep
  scope tight, but the *new* integrity-binding + version-gate logic ships as a single
  shared helper (`_verify_claims_binding`) so it never duplicates. Consolidating the
  older duplication is left as a follow-up.
- **[Mirroring the server's conditional `claims_raw` reopens the gate — fail-OPEN
  landmine]** → The server omits `claims_raw` from an `/execute` body only for
  hand-built test doubles (`if attestation_doc.claims_raw is not None:`), with a comment
  reading "None only … in some tests." A caller implementer could misread that as
  "`claims_raw` is optional on `/execute`" and tolerate its absence — turning the
  mandatory integrity gate back into a silent skip, so every stripped-preimage tamper (a
  wire attacker simply deleting `claims_raw`) sails through wearing the test double's
  costume. → **Mitigation:** the server's conditional is a server-internal serialization
  detail, **not** caller-side optionality. On `/execute` (and `/output`) the caller
  treats an absent `claims_raw` as reject — no questions asked, no test-double exception —
  because it cannot distinguish a tamperer from a test double and must not try. The
  `if claims_raw is not None` shape MUST NOT be mirrored on the caller.

## Migration Plan

1. Server side is already shipped (emits `v=1`, `schema_version="1.0"`, `claims_raw`).
2. Land this caller change: new `validate_execution_attestation`, `claims_raw` on
   `validate_output_attestation`, `_verify_claims_binding` helper, None-guards removed,
   canonical output digest, `/attest` left on bare `validate_attestation`.
3. Update `openspec/specs/attested-executor-caller/spec.md` deltas and the tests
   (A: wrong hash rejected; B: absent hash rejected; C: `/attest` still validates).
4. **Rollback:** revert the caller change; because the server is a separate deploy, a
   rollback here simply returns to incompatibility — so coordinate the revert with the
   server if the format is ever rolled back.

## Open Questions

- **~~Output-phase claims membership~~ — RESOLVED (against server `src/attestation.py`):**
  the output `claims_raw` is `{ schema_version, output_digest, security{…}, gpu{…}? }` —
  it does **not** carry `repository_url`/`commit_hash`/`script_path`/`script_env_hash`.
  So output binds only `output_digest` (+ `execution_id` from the envelope), and the
  execution fields are legitimately absent at output time. This makes the
  mandatory-presence set **per-phase** (D4/D3): the output validator MUST require only
  `output_digest` and MUST NOT require the execution fields, or it will false-reject
  every output attestation. The specs delta's output scenario must state this.
- **Attested-but-unbound `security` posture (future policy hook):** the nine-field
  `security` posture block is now attested and present in `claims_raw` for both phases,
  but the caller binds none of it (correctly — it is server-chosen, not caller-sent). It
  is therefore within reach for a *future* change to enforce a minimum-posture policy
  (e.g. reject unless `no_new_privileges` and `read_only_rootfs`). Out of scope here —
  flagged as a follow-up so the newly-available data is not forgotten.
- **~~Accepted MAJOR pinning~~ — RESOLVED (grounded in `attestation.py`'s existing
  constant convention):** three values are pinned as module-level scalars in
  `attestation.py` (co-located with the D5 gate `_verify_claims_binding` and matching the
  existing `MAX_ATTESTATION_B64_SIZE` style; no new `constants.py`):

  ```
    ACCEPTED_ENVELOPE_VERSION    = 1          # server ENVELOPE_VERSION
    ACCEPTED_CLAIMS_SCHEMA_MAJOR = 1          # server CLAIMS_SCHEMA_VERSION "1.0" → MAJOR
    SHA256_DIGEST_PREFIX = "sha256:"          # claims_digest AND inner output_digest
  ```

  **Scalar, not a set/range** — deliberately: a `frozenset` would whisper "multiple
  formats at once," contradicting the no-dual-format Non-Goal, whereas a scalar equality
  check *structurally enforces* the single-format cutover. A future MAJOR bump is a
  one-line reviewed change (`= 1` → `= 2`); a genuine transition window must consciously
  widen the type, making that architectural shift visible in review rather than sneaking
  in as a membership tweak. `SHA256_DIGEST_PREFIX` has three consumers — the
  `claims_digest` integrity check, the `output_digest` recompute, and the write-side
  canonical provenance form (`poll_output`/`artifact.py`, imported) — so stored ≡
  verified. The pinning comment names the server constants it mirrors, keeping a
  cross-repo MAJOR bump greppable on both sides.
- **`gpu` block surfacing:** beyond tolerating it, should the caller log the `gpu`
  claims or surface them in the job summary / attestation artifact? Out of scope for
  this change, but worth a follow-up if attested GPU identity becomes consumer-visible.
