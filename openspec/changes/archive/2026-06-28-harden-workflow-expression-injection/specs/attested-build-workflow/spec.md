## ADDED Requirements

### Requirement: Untrusted Inputs Are Not Interpolated Into Run Bodies

The workflow SHALL NOT interpolate any `${{ … }}` expression derived from
`workflow_dispatch` inputs or from the remote executor's stdout directly into a `run:`
script body (shell or `python3 -c`). Such values SHALL be passed through a step `env:`
block and referenced as quoted shell variables (`"$VAR"`) or read from `os.environ`, so
they are runtime data rather than evaluated script source.

#### Scenario: Dispatch inputs reach the shell only through env

- **WHEN** a step uses `inputs.server_url`, `inputs.script_path`, `inputs.commit_hash`, `inputs.repository_url`, or `inputs.audience`
- **THEN** the value is bound in the step's `env:` block and referenced as a quoted `"$VAR"` in the `run:` body, and no `${{ inputs.* }}` expression appears inside any `run:` body

#### Scenario: Remote-derived markers reach the shell only through env

- **WHEN** a step uses `steps.parse_markers.outputs.binary_oci_ref` (extracted from the executor's stdout)
- **THEN** the value is bound in the step's `env:` block and referenced as a quoted `"$VAR"`, not interpolated directly into the `run:` body

#### Scenario: Provenance generation reads values from the environment

- **WHEN** the provenance-manifest step runs its inline Python
- **THEN** the binary digest, repository URL, commit hash, and run id are read from `os.environ`, and no `${{ … }}` expression is interpolated into the Python source text

#### Scenario: Injection-bearing input cannot execute on the runner

- **WHEN** a dispatch supplies an input containing shell metacharacters (e.g. `"; <command>; "`)
- **THEN** the value is treated as inert data by every `run:` body and the embedded command does not execute on the runner

### Requirement: Remote OCI Reference Is Validated Before Use

The workflow SHALL validate the `BINARY_OCI_REF` parsed from the executor's stdout
against a strict, repository-pinned grammar before any step uses it, so a malicious or
malformed reference cannot redirect `oras pull` or `gh api` to an unexpected
registry or namespace.

#### Scenario: Reference matching the expected shape is accepted

- **WHEN** the parsed `BINARY_OCI_REF` matches `ghcr.io/<owner>/<repo-name>/tmp-build:<tag>` (owner and repo-name being this repository's, tag drawn from `[A-Za-z0-9._-]`)
- **THEN** the workflow records it as a step output and proceeds to pull the binary

#### Scenario: Out-of-grammar reference fails the job

- **WHEN** the parsed `BINARY_OCI_REF` does not match the expected registry, owner, repository, `tmp-build` package, or tag grammar
- **THEN** the workflow fails with a descriptive error before passing the value to `oras pull` or `gh api`

## MODIFIED Requirements

### Requirement: Server URL Validation and Allowlisting

The workflow SHALL validate `server_url` before proceeding and SHALL enforce an
optional allowlist. The value validated against the allowlist SHALL be the same value
subsequently passed to the caller; the workflow SHALL NOT re-interpolate the raw
`inputs.server_url` into a later `run:` body in a way that bypasses the allowlist check.

#### Scenario: Empty server_url is rejected

- **WHEN** `server_url` is empty
- **THEN** the workflow fails before contacting any executor

#### Scenario: Allowlist is enforced when provided

- **WHEN** `server_url_allowlist` is provided and `server_url` is not in the comma-separated list
- **THEN** the workflow rejects the request

#### Scenario: Validated server_url is the value passed to the caller

- **WHEN** the caller is invoked after validation
- **THEN** it receives the `server_url` via an `env:`-bound variable rather than a raw `${{ inputs.server_url }}` interpolation, so the allowlist decision cannot be bypassed by shell interpolation

### Requirement: Binary Retrieval and Integrity Verification

After a successful remote build, the workflow SHALL retrieve the binary from the
temporary GHCR package and verify its digest matches the build output. The
`BINARY_OCI_REF` used to pull the binary SHALL be validated against the
repository-pinned reference grammar before use.

#### Scenario: Markers are parsed, validated, and the binary is pulled

- **WHEN** the caller completes successfully
- **THEN** the workflow parses the execution stdout for `BINARY_OCI_REF` and `BINARY_SHA256`, validates `BINARY_OCI_REF` against the `ghcr.io/<owner>/<repo-name>/tmp-build:<tag>` grammar, and then pulls the binary from GHCR via oras using the validated reference passed through `env:`

#### Scenario: Digest mismatch fails the job

- **WHEN** the SHA-256 of the downloaded binary does not match the `BINARY_SHA256` from the build output
- **THEN** the workflow fails with a descriptive integrity-mismatch error

#### Scenario: Missing markers fail the job

- **WHEN** either `BINARY_OCI_REF` or `BINARY_SHA256` is absent from the execution stdout
- **THEN** the workflow fails with a descriptive error

#### Scenario: Malformed BINARY_OCI_REF fails the job

- **WHEN** `BINARY_OCI_REF` is present but does not match the expected reference grammar
- **THEN** the workflow fails with a descriptive error before invoking `oras pull`
