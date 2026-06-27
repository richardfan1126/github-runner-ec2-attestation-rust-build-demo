## Context

This repo began as a self-contained consumer that owned **both** halves of an attested
build:

- the build **environment** — `Dockerfile` → `build-image.yml` publishes
  `ghcr.io/<owner>/…/build-image@sha256:…`, which an operator manually pins into the
  executor's container config; and
- the build **workload** — `rust-project/` + `scripts/build-rust.sh` +
  `attested-rust-build.yml`, which dispatches a build to the executor and verifies/attests
  the result.

Three merged changes upstream in `github-runner-ec2-attestation` moved the environment half
out of consumer repos:

- `bake-image-into-ami` — the execution image is pulled at AMI-build time and baked offline
  into the dm-verity-sealed root, bound by image-ID and anchored to its GHCR manifest digest.
  Nothing is pulled by a live executor at runtime.
- `execution-build-images` — generalized that to **N flavors**, where a flavor is the
  co-located directory `flavors/<f>/` (`Dockerfile` + `env`), each producing its own
  PCR4-bound attestable AMI.
- `unify-image-build-producers` — the producer that copied this repo's hardened `Dockerfile`
  verbatim into `flavors/rust-build/Dockerfile` (only comments differ) and added
  `flavors/rust-build/env` naming this repo in `ALLOWED_REPOSITORIES`.

So upstream now owns the rust-build environment. This repo's `Dockerfile`, `build-image.yml`,
the `test_dockerfile_*` assertions, and the README "pin the build-image digest" guidance are
a second, drifting source of truth for an image that is no longer pulled at runtime.

## Goals / Non-Goals

**Goals:**
- Remove this repo's duplicate, drift-prone ownership of the build image and its publishing CI.
- Replace the obsolete "pin `build-image@sha256:…` into the executor" operator model with
  guidance pointing at the upstream `rust-build` flavor and its attestable AMI.
- Make the toolchain contract that `scripts/build-rust.sh` silently depends on **explicit**,
  so divergence from the upstream flavor is a documented, checkable breakage rather than a
  mystery runtime failure.

**Non-Goals:**
- Changing the workload: `rust-project/`, the behavior of `scripts/build-rust.sh`, and
  `attested-rust-build.yml` are untouched (the US1 `test_build_script_*` cases stay).
- Reconciling the toolchain *values* themselves — today the upstream copy is byte-identical,
  so no version/path change is needed; this change only pins the contract.
- Owning or duplicating any part of the upstream flavor/AMI pipeline in this repo.

## Decisions

### D1 — Delete rather than vendor the Dockerfile

**Decision:** remove `Dockerfile` outright; do not keep a "reference" copy.
**Rationale:** a copy with no CI building it is precisely the drift hazard this change exists
to remove — it looks authoritative but nothing tests or publishes it. The authoritative
artifact is `github-runner-ec2-attestation/flavors/rust-build/Dockerfile`.
**Alternative considered:** keep the Dockerfile as documentation-only. Rejected: it re-creates
two sources of truth and the existing `test_dockerfile_*` cases would keep asserting against
the wrong copy.

### D2 — Express the toolchain dependency as a contract requirement, not a test of a local file

**Decision:** add one spec requirement capturing what the build script needs from the image
(`oras`, `cargo`/`rustc`/`cc`/`curl` on PATH for `65534`; `RUSTUP_HOME=/opt/rust`; toolchain
bin at `/opt/rust/toolchains/1.96.0-…/bin`) and name the upstream `rust-build` flavor as its
provider. Document the same contract where `build-rust.sh` declares its `RUSTUP_HOME` /
`RUST_TOOLCHAIN_BIN` defaults.
**Rationale:** the script already hard-codes these literals (`build-rust.sh:69,74`); once the
Dockerfile leaves, nothing else records *why* those values are correct. A contract requirement
keeps the expectation testable (e.g. a future smoke check against the published flavor image)
without re-owning the image.
**Alternative considered:** a CI job that pulls the upstream flavor image and runs the build
script against it. Deferred as out of scope — valuable but a separable follow-up; this change
only establishes the contract it would verify.

### D3 — Rewrite, not delete, the operator documentation requirement

**Decision:** keep an "Operator Documentation" requirement but rewrite it: drop the
`build-image@sha256:…` pin/re-pin scenario; require docs that point operators at the upstream
`rust-build` flavor and the per-flavor attestable AMI, and retain the scratch-size and
"no security changes needed" guidance (still true).
**Rationale:** operators still need to know how to run this build; only the *mechanism* changed
(AMI-baked flavor vs. runtime-pinned image). Deleting the requirement would leave a doc gap.

## Risks / Trade-offs

- **Hidden coupling to upstream** → the toolchain contract requirement (D2) plus README
  pointer make the dependency explicit; a follow-up smoke check can enforce it.
- **Upstream flavor drifts from `build-rust.sh` literals** (e.g. Rust `1.96.0` bumped, or
  `oras` dropped) → breaks this repo's build with no local signal. Mitigation: the contract
  requirement names the exact literals so a diff against `flavors/rust-build/Dockerfile` is a
  defined check; escalate to the deferred smoke job if drift recurs.
- **Lost local hardening assertions** → the `test_dockerfile_*` cases move upstream with the
  Dockerfile (already covered by the flavor's own hardening expectations); the US1
  `test_build_script_*` cases remain here unchanged.
- **README churn confuses in-flight operators** → call out the model change explicitly in the
  rewritten section (was: pin a digest; now: select the flavor / AMI).

## Migration Plan

1. Land the spec delta and README rewrite first (doc/source-of-truth flip).
2. Remove `Dockerfile`, `build-image.yml`, and the `test_dockerfile_*` cases.
3. Confirm `pytest` still passes with only the US1 build-script cases in
   `tests/test_build_image_hardening.py`.
4. Rollback: restore the three files from git history; the upstream flavor is unaffected
   either way.

## Open Questions

- Should a CI smoke job pull `flavors/rust-build`'s published image by digest and run
  `build-rust.sh` against it to actively enforce the toolchain contract? (Deferred; see D2.)
- Where should the canonical toolchain-contract values live so both repos can reference one
  source — this spec, or a shared doc in the upstream repo?
