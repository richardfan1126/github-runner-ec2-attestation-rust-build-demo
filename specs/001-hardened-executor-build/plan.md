# Implementation Plan: Hardened-Executor-Compatible Attested Build

**Branch**: `001-hardened-executor-build` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-hardened-executor-build/spec.md`

## Summary

Today the attested Rust build only works if the operator weakens the Remote Executor (allowing root + a writable root filesystem so `build-rust.sh` can `apt-get install` curl/build-essential and `rustup`-install the toolchain at run time). This feature removes that requirement by moving **all tool provisioning out of run time and into a purpose-built, digest-pinned container image**, and by rewriting the build script to run rootless (UID:GID `65534:65534`) under a read-only root filesystem and read-only workspace, writing **only** to a single executor-provided tmpfs scratch mount.

Two deliverables in *this* repo: (1) a `Dockerfile` (pinned base + pinned cargo/rustc, cc, curl, oras) published to GHCR by a new CI workflow that surfaces the immutable digest; (2) a hardened `scripts/build-rust.sh` that drops the install steps, relocates `CARGO_HOME`/`RUSTUP_HOME`/`CARGO_TARGET_DIR` under scratch, preflights tool presence with attributable errors, and keeps the existing `BINARY_SHA256`/`BINARY_OCI_REF` markers and oras-push contract unchanged. README gains the operator guidance (image digest reference + ≥4 GiB scratch floor). The Remote Executor repo is **not** modified.

## Technical Context

**Language/Version**: Bash (build script + CI glue); Rust **pinned stable** toolchain inside the image (target binary `attested-hello`, edition 2021, **zero crate dependencies** per `Cargo.lock`); Python 3.11 (existing caller/test harness, unchanged by this feature).

**Primary Dependencies**: Docker/BuildKit (image build); rustup (image-build-time only, pinned channel); a C compiler/linker (`cc`/`gcc` + `ld`) for the final link; `curl`; `oras` CLI 1.3.2. No new Python deps.

**Storage**: N/A (no datastore). Ephemeral writes land in the tmpfs scratch mount only.

**Testing**: `pytest` (existing). New tests are static/property checks over `Dockerfile` and `scripts/build-rust.sh` text (pinning present, no run-time installs, scratch-only writes, attributable-error messages), matching the existing text-inspection style in `tests/test_security_hardening.py`. Container/integration validation is documented as manual steps in `quickstart.md` (requires Docker, out of CI scope here).

**Target Platform**: linux/amd64 execution container running the executor's hardened defaults: non-root `65534:65534`, read-only rootfs, read-only `/workspace`, `no-new-privileges`, default capability set only, single writable tmpfs scratch mount, outbound egress permitted.

**Project Type**: Single repo — container image + shell build script + CI workflow + docs. No application source layout change.

**Performance Goals**: N/A beyond "a Rust release build of `attested-hello` fits within the documented ≥4 GiB scratch floor." Build time is not a tracked metric.

**Constraints**:
- Rootless `65534:65534`; **no** added Linux capabilities (build relies on none); `no-new-privileges`.
- Read-only rootfs **and** read-only workspace; **every** write under the single tmpfs scratch mount (`CARGO_HOME`, `RUSTUP_HOME` usage, `CARGO_TARGET_DIR`, downloads, oras auth/scratch).
- Zero run-time software installation (no package manager, no toolchain download).
- All image-build downloads SHA-256-verified; base image + tools version/digest-pinned.
- Preserve `BINARY_SHA256:<digest>` / `BINARY_OCI_REF:<ref>` stdout markers and the oras-push transfer mechanism exactly (workflow consumer is unchanged).
- Egress required only for the final GHCR push; its failure must be surfaced clearly (out of scope to remove).

**Scale/Scope**: One image, one build script rewrite, one new CI workflow, README section, and a focused test module. The external Remote Executor is a fixed dependency, not modified.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution (`.specify/memory/constitution.md`) is an **unratified template** (placeholder principles only). There are therefore no concrete, enforceable gates to evaluate. No violations can be declared against placeholders, and none are asserted.

Applying the spirit of the template headings (Simplicity / YAGNI, observable text I/O, testability):

- **Simplicity**: The design moves complexity from run time into a reproducible build-time image and *deletes* the run-time install path — a net reduction in moving parts. PASS.
- **Observable text I/O**: The build keeps stdout markers and emits attributable errors to stderr with non-zero exit. PASS.
- **Testability**: Pinning and hardening invariants are asserted by static tests; end-to-end behavior has a runnable quickstart. PASS.

**Gate result**: PASS (no ratified constraints; no unjustified complexity). Re-checked post-Phase 1 — still PASS; no new projects, patterns, or abstractions introduced.

## Project Structure

### Documentation (this feature)

```text
specs/001-hardened-executor-build/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 output — decisions resolving implementation unknowns
├── data-model.md        # Phase 1 output — entities, attributes, states
├── quickstart.md        # Phase 1 output — runnable hardened-build validation
├── contracts/           # Phase 1 output — interface contracts
│   ├── build-script.md      # env inputs, stdout markers, exit codes, write locations
│   ├── build-image.md       # tools/versions/user/PATH the image guarantees
│   └── image-publish-ci.md  # CI workflow trigger, outputs (immutable digest)
├── spec.md              # Feature specification (input)
└── checklists/          # Existing author self-check checklists
```

### Source Code (repository root)

```text
Dockerfile                              # NEW — purpose-built, digest-pinned build image (FR-001..FR-006)
.dockerignore                           # NEW — keep image context minimal/reproducible
scripts/
└── build-rust.sh                       # MODIFIED — drop installs; rootless; scratch-only writes; preflight (FR-007..FR-016b)
.github/workflows/
├── attested-rust-build.yml             # UNCHANGED — consumer of markers/oras push stays compatible (FR-013, FR-014, SC-007)
└── build-image.yml                     # NEW — build Dockerfile, push to GHCR, surface immutable digest (FR-005, FR-005a)
rust-project/                           # UNCHANGED — attested-hello (zero deps)
├── Cargo.toml
├── Cargo.lock
├── build.rs
└── src/main.rs
tests/
└── test_build_image_hardening.py       # NEW — static/property checks of Dockerfile + build-rust.sh invariants
README.md                               # MODIFIED — operator guidance: image digest ref + ≥4 GiB scratch (FR-017..FR-019a)
```

**Structure Decision**: Single-repo layout, no new top-level modules. The image (`Dockerfile`) and the run-time logic (`scripts/build-rust.sh`) are the two primary artifacts named in the spec's Scope; a new `build-image.yml` workflow publishes the image and the existing `attested-rust-build.yml` is deliberately left untouched to honor the marker/oras-push compatibility requirements. New tests live alongside the existing `pytest` suite under `tests/`.

## Complexity Tracking

> No constitution violations to justify (constitution is an unratified template). Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
