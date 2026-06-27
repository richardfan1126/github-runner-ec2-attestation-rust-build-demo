## Why

The build *environment* (toolchain container image) is no longer owned by this repo.
Upstream `github-runner-ec2-attestation` now builds execution images from a co-located
`flavors/<f>/` directory and bakes each into a per-flavor, PCR4-bound attestable AMI
(`bake-image-into-ami` → `execution-build-images` → `unify-image-build-producers`). Its
`flavors/rust-build/Dockerfile` is a byte-for-byte copy of this repo's `Dockerfile`
(only comments differ), and its `flavors/rust-build/env` already authorizes this repo in
`ALLOWED_REPOSITORIES`. This repo's local image-build path is now a duplicate source of
truth that will silently drift from the real flavor, and its operator docs describe a
"pin the build-image digest into the executor" model that no longer exists.

## What Changes

- **BREAKING** Remove this repo's ownership of the build image. The Dockerfile, its
  publishing workflow, and the operator setup docs are superseded by the upstream
  `flavors/rust-build/` pipeline.
  - Delete `Dockerfile` (now owned upstream as `flavors/rust-build/Dockerfile`).
  - Delete `.github/workflows/build-image.yml` ("Build Hardened Build Image") — the
    upstream flavor→AMI bake replaces building/pushing a runtime-pinnable `build-image`.
  - Delete the `Dockerfile`-asserting tests in `tests/test_build_image_hardening.py`
    (the US2 `test_dockerfile_*` cases); keep the US1 `test_build_script_*` cases.
  - Rewrite the README "Configuring the Hardened Build Image (Operator Setup)" section:
    remove the `build-image@sha256:…` pin/re-pin guidance; point operators at the
    upstream flavor and its attestable AMI instead.
- Establish an explicit **toolchain contract** the build script depends on, now that this
  repo no longer owns the Dockerfile that guaranteed it: `oras`, `cargo`/`rustc`/`cc`/
  `curl` on PATH, `RUSTUP_HOME=/opt/rust`, and the `1.96.0` toolchain bin path. Capture it
  as a documented dependency on the `rust-build` flavor so drift is detectable.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `hardened-build-environment`: the capability stops owning the build *image* and its
  in-repo publishing CI. The "Build Image Ships All Tools Pre-Installed", "Build Image Is
  Reproducible and Pinned", "Tools Are Usable by the Unprivileged Default User", and
  "Operator Documentation for Executor Configuration" requirements are removed or rewritten
  to reference the upstream `rust-build` flavor; a new requirement pins the toolchain
  contract the retained build script relies on. The build-script, Rust-project, scratch,
  env-var, transfer, and failure-mode requirements are unchanged.

## Impact

- **Files removed:** `Dockerfile`, `.github/workflows/build-image.yml`, the
  `test_dockerfile_*` cases in `tests/test_build_image_hardening.py`.
- **Files changed:** `README.md` (operator-setup section), `scripts/build-rust.sh` (only
  if the toolchain contract is documented inline), `openspec/specs/hardened-build-environment/spec.md`.
- **No change** to `rust-project/`, `scripts/build-rust.sh` behavior, or
  `.github/workflows/attested-rust-build.yml` (the workload/dispatch side).
- **External dependency introduced:** correctness now depends on upstream
  `flavors/rust-build/` keeping the documented toolchain contract; a divergence there
  breaks this repo's build with no local signal.
