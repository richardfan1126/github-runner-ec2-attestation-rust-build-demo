# Security Review

Date: 2026-06-27

## Scope

This review covers the consumer repository
`github-runner-ec2-attestation-rust-build-demo`: the attested build workflow
(`.github/workflows/attested-rust-build.yml`), the `call_remote_executor` client
(`.github/scripts/call_remote_executor/`), the in-container build script
(`scripts/build-rust.sh`), and the helper scripts (`validate_allowlist.py`,
`create_provenance.py`).

The Remote Executor server (the attestable EC2 service this repository calls) is
reviewed separately in the `github-runner-ec2-attestation` repository's
`SECURITY_REVIEW.md`.

## Findings

### 1. High: GitHub Actions expression injection via `workflow_dispatch` inputs

- `.github/workflows/attested-rust-build.yml` interpolates untrusted `${{ inputs.* }}`
  directly into `run:` shell bodies in step 7 (Run Remote Executor Caller), step 11
  (Create provenance manifest), and step 13 (Push OCI artifact): `inputs.server_url`,
  `inputs.script_path`, `inputs.commit_hash`, `inputs.repository_url`, and
  `inputs.audience`.
- Step 11 additionally embeds `${REPO_URL}` / `${COMMIT_HASH}` (derived from those
  inputs) into a `python3 -c "..."` string, adding a second (Python) injection surface.
- An actor able to dispatch the workflow can inject arbitrary commands on the runner,
  which holds `secrets.GITHUB_TOKEN` with `packages: write`, `attestations: write`, and
  `id-token: write`.
- The server-URL allowlist enforced in step 1 is bypassed: step 1 validates an `env`
  copy, but step 7 re-interpolates the raw `${{ inputs.server_url }}`.
- `${{ steps.parse_markers.outputs.binary_oci_ref }}` (matched as `.+` from attested
  build stdout) is interpolated into `oras pull` in step 9 without a registry-reference
  format check.
- Required hardening: pass every input through `env:` and reference quoted `"$VAR"` in
  the script (as step 1 already does correctly); never interpolate `${{ inputs.* }}`
  inside a `run:` body; validate `binary_oci_ref` against a strict grammar before use.
- Impact: a workflow-dispatch actor can execute code on the runner and abuse the
  workflow's write-scoped `GITHUB_TOKEN`, defeating the step-1 allowlist control.

### 2. Medium: Caller attestation primitives fail open on empty policy inputs

- In `call_remote_executor/attestation.py`, `verify_certificate_chain` and
  `verify_cose_signature` `return` (skip) when `root_cert_pem` is falsy, and
  `validate_pcrs` returns when `expected_pcrs` is falsy.
- Production is currently safe because `cli.py` marks `--root-cert-pem` and
  `--expected-pcrs` as `required=True` and `RemoteExecutorCaller.__init__` rejects empty
  values. The risk is that the security-critical functions themselves are fail-open.
- A future caller, a test helper imported into a real path, or a refactor passing
  `expected_pcrs={}` or an empty root would silently disable PKI + COSE signature + PCR
  verification and accept a forged attestation from any server, with no error.
- Required hardening: make these functions fail closed (raise) on missing policy input;
  gate any verification skip behind an explicit, test-only flag rather than an empty
  argument.
- Impact: defense-in-depth gap at the trust anchor; a single empty-string regression
  collapses the entire attestation guarantee.

### 3. Low: OIDC token request URL built with unencoded audience

- `caller.py request_oidc_token()` builds `url = f"{request_url}&audience={self.audience}"`
  without URL-encoding the audience.
- Operator-controlled value, low risk, but should be encoded via `urllib.parse.quote`.
- Impact: malformed or crafted audience values could alter the OIDC request URL.

### 4. Informational: write-scoped token handed to remote container with egress

- The workflow forwards `secrets.GITHUB_TOKEN` to the enclave via
  `--script-env GITHUB_TOKEN=...`; the `rust-build` flavor `flavors.lock` (in the server
  repository) relaxes `container_network_mode: bridge` and `container_tmpfs_exec: true`.
- This is inherent to "build pushes the artifact to GHCR" and is surfaced in the
  attestation `user_data` (the consumer can see the relaxations), consistent with the
  documented threat model.
- Recommendation: track a least-privilege / narrowly-scoped token for the build so that
  a malicious commit's build script cannot exfiltrate a broadly-scoped token over the
  egress path.

## Strengths confirmed

- The caller pins the server's composite public key by SHA-256 fingerprint carried in
  the signed attestation document before deriving the PQ-hybrid channel
  (`verify_server_key_fingerprint`), eliminating trust-on-first-use.
- Certificate-chain validation adds only the pinned AWS Nitro root as a trust anchor and
  treats all `cabundle` entries as untrusted intermediates, preventing a malicious server
  from injecting its own CA.
- Execution-acceptance and output-integrity attestations are bound to the request fields
  (`repository_url`, `commit_hash`, `script_path`, `script_env_hash`, `execution_id`) and
  to a fresh per-request nonce; output integrity is verified against a recomputed
  SHA-256 of the canonical stdout/stderr/exit-code.
- Output attestation is fail-closed by default; degraded mode requires an explicit
  `--allow-missing-output-attestation` flag.

## Coverage gaps

- No test asserts the workflow avoids `${{ inputs.* }}` interpolation in `run:` bodies
  (finding 1).
- No test asserts the caller's `verify_certificate_chain` / `verify_cose_signature` /
  `validate_pcrs` fail closed when policy inputs are empty (finding 2).
- No test asserts `binary_oci_ref` is validated against a registry-reference grammar
  before use in `oras pull`.
