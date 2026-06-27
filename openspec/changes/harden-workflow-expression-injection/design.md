## Context

`attested-rust-build.yml` mixes three trust levels into `run:` script bodies:

```
                          interpolated INTO run: body              trust
 ──────────────────────────────────────────────────────────────────────────────
 Step 7  caller      server_url, script_path, commit_hash,        UNTRUSTED
                     repository_url, audience, github.repository    (dispatch inputs)
 Step 11 provenance  commit_hash, repository_url, binary_sha256    UNTRUSTED
         (shell + python3 -c)                                       + PYTHON surface
 Step 13 push        commit_hash                                   UNTRUSTED
 Step 9  oras pull   binary_oci_ref   ◄── matched as `.+`          UNTRUSTED
 Step 17 cleanup     binary_oci_ref       from remote stdout        (remote-controlled)
 ──────────────────────────────────────────────────────────────────────────────
 Steps 10/16  binary_sha256 (already hex[64]-constrained)         lower
 various      github.run_id / actor / owner / repo.name           low (best-practice env)
```

GitHub Actions substitutes `${{ … }}` into the script source before the shell parses
it, so any untrusted value containing shell metacharacters becomes executable text.
Step 1 already demonstrates the correct mitigation: bind the expression to an `env:`
variable and reference `"$VAR"`, so the value is data the shell reads at runtime, never
script it parses.

## Goals / Non-Goals

**Goals:**
- Remove every interpolation of a `workflow_dispatch` input or remote-executor-derived
  value from a `run:` (shell or `python3 -c`) body.
- Validate `BINARY_OCI_REF` against a strict grammar before it is used.
- Close the allowlist bypass (Step 1 validates, Step 7 must consume the same value).

**Non-Goals:**
- Changing build/sign/attest/publish behavior for well-formed inputs.
- Reworking the caller (`call_remote_executor`) or its attestation logic — findings 2–4
  of `SECURITY_REVIEW.md` are separate changes.
- Narrowing the `GITHUB_TOKEN` scope handed to the enclave (finding 4, informational).

## Decisions

### D1 — `env:` indirection for every untrusted value, quoted `"$VAR"` in the body

**Decision:** move all untrusted (and, for consistency, GitHub-context) expressions into
each step's `env:` block and reference them as quoted shell variables. Expressions with
`||` fallbacks and `format(...)` are safe to place in `env:` values — the expression is
evaluated and the *result* is bound to the variable, not re-injected as script. E.g.:

```yaml
env:
  SERVER_URL:     ${{ inputs.server_url }}
  SCRIPT_PATH:    ${{ inputs.script_path  || 'scripts/build-rust.sh' }}
  COMMIT_HASH:    ${{ inputs.commit_hash  || github.sha }}
  REPOSITORY_URL: ${{ inputs.repository_url || format('https://github.com/{0}', github.repository) }}
  AUDIENCE:       ${{ inputs.audience }}
run: |
  python .github/scripts/call_remote_executor --server-url "$SERVER_URL" ...
```

**Rationale:** this is the canonical GitHub-documented mitigation and the pattern Step 1
already uses, so it is consistent and low-risk. It also closes the allowlist bypass for
free: Step 7 reads the same `inputs.server_url`-derived env value the workflow validated,
rather than re-pasting the raw input.

### D2 — Step 11 reads from `os.environ`, never from interpolated source

**Decision:** pass `binary_sha256`, `repo_url`, `commit_hash`, and `run_id` via `env:`
and have the inline Python read `os.environ[...]`:

```python
import os, json, datetime
from scripts.create_provenance import create_provenance_manifest
manifest = create_provenance_manifest(
    binary_name='attested-hello',
    sha256=os.environ['BINARY_SHA256'],
    repo_url=os.environ['REPO_URL'],
    commit_hash=os.environ['COMMIT_HASH'],
    run_id=os.environ['RUN_ID'],
    timestamp=datetime.datetime.utcnow().isoformat() + 'Z',
)
```

**Rationale:** collapses both the shell *and* the Python injection surface — nothing is
interpolated into source text on either layer; values arrive as environment data at
runtime.
**Alternative considered:** promote the `-c` block to a small committed helper script
invoked with arguments. Cleaner still, but a larger change than needed to close the
finding; the `os.environ` form is sufficient and minimal. Left as a possible follow-up.

### D3 — Validate `BINARY_OCI_REF` against a strict, repo-pinned grammar at parse time

**Decision:** in Step 8 (parse markers), after extracting `BINARY_OCI_REF`, reject any
value that does not match the expected shape before writing it to `$GITHUB_OUTPUT`. The
expected producer is the remote build pushing a temporary package the cleanup steps
already assume is `ghcr.io/<owner>/<repo-name>/tmp-build:<tag>` (see Step 17's path
parsing and Step 18's `package-name: <repo>/tmp-build`). Validate against:

```
^ghcr\.io/<owner>/<repo-name>/tmp-build:[A-Za-z0-9._-]{1,128}$
```

with `<owner>` = `github.repository_owner` and `<repo-name>` = the repository name,
both passed in via `env:` (and themselves regex-escaped or compared literally, not
interpolated into the pattern as code).

**Rationale:** the remote executor's stdout is the least-trusted input in the workflow.
Pinning the registry, owner, repository, and `tmp-build` package — not merely "some OCI
reference" — gives defense in depth: even a compromised build cannot redirect `oras
pull` or `gh api` to an arbitrary registry/namespace. Steps 9 and 17 then consume only a
validated value (still via `env:` per D1).
**Alternative considered:** a loose OCI-reference grammar
(`^ghcr\.io/[a-z0-9._/-]+:[A-Za-z0-9._-]+$`). Rejected as the default: it blocks shell
injection but still permits the remote to name any ghcr.io namespace. The repo-pinned
form is preferred because the expected ref is fully known.

## Risks / Trade-offs

- **Over-strict grammar breaks a valid flow** → if the remote executor ever pushes the
  temporary binary under a different namespace/tag scheme than
  `<owner>/<repo>/tmp-build:<tag>`, Step 8 will fail the job. Mitigation: the grammar is
  derived from the cleanup steps' existing assumptions, so it matches today's producer;
  if the producer contract changes, the grammar (and the cleanup parsing) change
  together. Confirm against a real run before merging (tasks §1).
- **Env-block sprawl** → each touched step grows an `env:` block. Accepted: it is the
  documented, auditable pattern and mirrors Step 1.
- **GitHub-context values are low-risk** → `github.run_id`, `actor`, `repository_owner`,
  `repo.name` are not dispatch-controlled; moving them to `env:` is best-practice
  consistency, not a fix for an active vector. Done for uniformity, not treated as the
  security-critical part.

## Migration Plan

1. Confirm a real dispatch's `BINARY_OCI_REF` matches the pinned grammar (capture from a
   prior successful run) so D3 does not break the happy path.
2. Apply the `env:` indirection (D1) to Steps 7, 9, 13, 16, 17.
3. Apply the `os.environ` rewrite (D2) to Step 11.
4. Add `BINARY_OCI_REF` grammar validation (D3) to Step 8.
5. Lint with `actionlint` (if available) and re-read every `run:` body to confirm no
   `${{ inputs.* }}` or `${{ steps.*.outputs.* }}` remains inside a script body.
6. Rollback: revert the workflow file; no state or downstream artifact is affected.

## Open Questions

- Should Step 11's inline `python3 -c` be promoted to a committed helper script (D2
  alternative) as a follow-up, for readability and testability?
- Is a CI lint (e.g. `actionlint` + a custom check) worth adding to assert no untrusted
  `${{ … }}` appears in `run:` bodies, so the fix can't silently regress? (This is the
  "no test asserts…" coverage gap from the review.)
