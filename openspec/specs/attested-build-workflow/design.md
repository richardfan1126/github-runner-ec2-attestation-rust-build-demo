# attested-build-workflow Design

Rationale behind [[attested-build-workflow]]. Distilled from the Kiro
`rust-attestated-build` design's key decisions.

## Context

The workflow orchestrates an end-to-end attested build: it dispatches a build onto
the attested Remote Executor (via [[attested-executor-caller]]), retrieves the
compiled binary, verifies its integrity, signs it with the attestation bundle,
publishes the final OCI artifact to GHCR, and produces a GitHub artifact
attestation. The binary is built inside an AWS Nitro Enclave that mounts the repo
read-only and runs the build under hardened defaults
([[hardened-build-environment]]). The challenge the design solves is getting the
binary *out* of the enclave and proving, end to end, where it came from.

## Goals / Non-Goals

**Goals:**

- One `workflow_dispatch` produces a verifiably attested Rust binary on GHCR.
- The binary that ships is byte-for-byte the one built in the enclave (integrity
  verified by digest round-trip).
- Two independent provenance layers: enclave attestation + GitHub/Sigstore
  attestation.
- No orphaned temporary packages left in the registry.

**Non-Goals:**

- Re-implementing the attested channel — that lives in
  [[attested-executor-caller]].
- Building the binary itself or provisioning the toolchain —
  [[hardened-build-environment]].

## Decisions

### GHCR as the enclave→workflow transfer mechanism

The build script pushes the compiled binary to a temporary OCI reference
(`ghcr.io/<repo>/tmp-build:<tag>`) from inside the enclave using `GITHUB_TOKEN`;
the workflow pulls it back with oras. This avoids the deprecated GitHub Actions
Artifacts v3 API and needs only `GITHUB_TOKEN` (no `ACTIONS_RUNTIME_TOKEN`/`_URL`,
which aren't available inside the enclave). The temporary package is cleaned up
afterward.

*Trade-off:* introduces a temporary registry artifact that must be GC'd (see
cleanup) and a network dependency, in exchange for a transport that works from
inside the enclave with the credentials already in scope.

### Verify the digest round-trip before trusting the binary

The build script prints `BINARY_SHA256`; the workflow recomputes the digest of the
pulled binary and fails on mismatch. This makes the GHCR hop tamper-evident — the
binary that gets signed and published is provably the one built in the enclave, not
something substituted in the registry in between.

### Oras everywhere, not Docker or raw OCI API

Both the in-enclave script and the workflow use the oras CLI for push/pull. The
binary is an *arbitrary artifact* with attestation-metadata layers, not a container
image; oras handles the OCI Distribution details (blob upload, manifest creation)
transparently and identically on both ends.

### Two-layer attestation

The Nitro attestation bundle proves the binary was *built in a trusted enclave*;
the GitHub Artifact Attestation (Sigstore, via `actions/attest@v4` with
`push-to-registry: true`) proves the *OCI artifact was produced by a specific
workflow run*. The two answer different questions (build environment vs. build
provenance) and are both attached so consumers can verify either.

### GitHub attestation is best-effort, not job-fatal

The `actions/attest@v4` step uses `continue-on-error: true`; a later step inspects
the outcome and emits a `::warning::` instead of failing the job. The enclave
attestation and the published artifact already carry the primary provenance, so a
transient Sigstore/attestation failure should warn rather than discard a good
build.

### Cleanup via version-ID lookup + delete-package-versions, always-run

A step looks up the temporary package's version ID by tag (trying the user packages
API, falling back to the org endpoint), then `actions/delete-package-versions@v5`
deletes only that version under `if: always()` + `continue-on-error: true`.
Deleting only the specific tag avoids disturbing other concurrent temporary builds;
always-run ensures cleanup on both success and failure; continue-on-error means an
already-deleted package or a permissions hiccup never fails the job.

### Bind commit_hash to the OIDC sha claim

The workflow always passes `github.sha` as `commit_hash` so it matches the OIDC
token's `sha` claim the executor verifies server-side (else HTTP 403). This keeps
the attested request bound to the actual workflow run.

## Risks / Trade-offs

- **Temporary package leakage** if cleanup is skipped entirely (e.g. the runner is
  killed before the always-step) — mitigated by unique per-build tags so leaked
  artifacts don't collide, but they can still accumulate.
- **Best-effort GitHub attestation** means an artifact can ship with only the
  enclave layer if Sigstore is down; accepted because the enclave attestation is
  the stronger, primary proof.
- **Registry as transport** couples the build to GHCR availability at two points
  (transfer and final publish); accepted as the single network dependency.
