## 1. Pre-flight

- [ ] 1.1 Capture a `BINARY_OCI_REF` value from a prior successful run (or the remote
  executor's documented contract) and confirm it matches
  `ghcr.io/<owner>/<repo-name>/tmp-build:<tag>`, so the Step 8 grammar (D3) does not
  break the happy path.
- [ ] 1.2 Inventory every `${{ … }}` interpolation inside a `run:` body in
  `attested-rust-build.yml` and tag each as untrusted (dispatch input /
  remote-stdout-derived), GitHub-context, or already-constrained — confirms the Steps
  7, 8, 9, 11, 13, 16, 17 scope.

## 2. Spec delta

- [ ] 2.1 Add the "Untrusted Inputs Are Not Interpolated Into Run Bodies" requirement and
  the modified "Server URL Validation and Allowlisting" / "Binary Retrieval and Integrity
  Verification" requirements in
  `openspec/changes/harden-workflow-expression-injection/specs/attested-build-workflow/spec.md`.
- [ ] 2.2 Validate the delta: `openspec validate harden-workflow-expression-injection --strict`.

## 3. Shell injection — env: indirection (D1)

- [ ] 3.1 Step 7 (Run Remote Executor Caller): move `inputs.server_url`,
  `inputs.script_path`, `inputs.commit_hash`, `inputs.repository_url`, `inputs.audience`,
  and `github.repository` into the step `env:` block; reference quoted `"$VAR"` in the
  `run:` body (keep the existing `GITHUB_TOKEN` env entry).
- [ ] 3.2 Step 13 (Push OCI artifact): move `inputs.commit_hash`,
  `github.repository_owner`, and `github.event.repository.name` into `env:`; reference
  `"$VAR"`.
- [ ] 3.3 Step 16 (Print job summary): move `steps.parse_markers.outputs.binary_sha256`
  and the `steps.oras_push.outputs.*` / `steps.attest.outcome` values into `env:`;
  reference `"$VAR"`.
- [ ] 3.4 Confirm no `run:` body in Steps 7/13/16 contains a bare `${{ … }}` expression.

## 4. Python injection — os.environ (D2)

- [ ] 4.1 Step 11 (Create provenance manifest): move `binary_sha256`, the derived
  `REPO_URL`/`COMMIT_HASH`, and `github.run_id` into `env:`.
- [ ] 4.2 Rewrite the inline `python3 -c` script to read `os.environ['BINARY_SHA256']`,
  `os.environ['REPO_URL']`, `os.environ['COMMIT_HASH']`, `os.environ['RUN_ID']` — no
  value interpolated into the Python source text; no `${{ … }}` left in the `run:` body.

## 5. BINARY_OCI_REF grammar validation (D3)

- [ ] 5.1 Step 8 (Parse stdout markers): after extracting `BINARY_OCI_REF`, validate it
  against `^ghcr\.io/<owner>/<repo-name>/tmp-build:[A-Za-z0-9._-]{1,128}$`, with
  `<owner>`/`<repo-name>` supplied via `env:` and compared literally (not interpolated
  into the pattern as code); fail the job with a descriptive error on mismatch.
- [ ] 5.2 Step 9 (Pull temporary binary) and Step 17 (Get temporary package version ID):
  read `steps.parse_markers.outputs.binary_oci_ref` via `env:` and reference `"$VAR"`,
  relying on the value already validated in Step 8.

## 6. Verify

- [ ] 6.1 Grep `attested-rust-build.yml` for `${{ inputs.` and
  `${{ steps.parse_markers.outputs.binary_oci_ref` inside `run:` bodies — confirm none
  remain (Step 1's `env:` usage is the only place inputs appear).
- [ ] 6.2 Run `actionlint` (if available) on the workflow; resolve any new findings.
- [ ] 6.3 Re-run `openspec validate harden-workflow-expression-injection --strict`.
- [ ] 6.4 Update `SECURITY_REVIEW.md` finding 1 and the related "Coverage gaps" entries
  to reflect the mitigation (or note the follow-up lint check if deferred).
