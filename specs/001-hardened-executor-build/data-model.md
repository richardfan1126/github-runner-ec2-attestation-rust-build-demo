# Phase 1 Data Model: Hardened-Executor-Compatible Attested Build

**Feature**: `001-hardened-executor-build` | **Date**: 2026-06-15

This feature has no application datastore. The "entities" are the build/supply-chain artifacts and the configuration surfaces named in the spec's **Key Entities**. Each is described by its attributes, validation rules (traced to FRs), and — where it has one — its lifecycle.

---

## Entity: Build image

The purpose-built container image defined by the in-repo `Dockerfile` and published to GHCR.

| Attribute | Value / Rule | Trace |
|---|---|---|
| Base image | Pinned by content **digest** (`FROM debian:bookworm-slim@sha256:…`), never a floating tag | FR-004a, R2 |
| Rust toolchain | `rustup`-installed, **exact** stable channel (e.g. `1.96.0`), `--profile minimal` | FR-002, FR-004, R3 |
| C compiler/linker | Exact version-pinned distro package(s) (`gcc=<ver>`, `libc6-dev=<ver>`) | FR-002, FR-004 |
| curl | Exact version-pinned distro package (`curl=<ver>`) | FR-002, FR-004 |
| oras CLI | Pinned `1.3.2` tarball, **SHA-256-verified** before extract | FR-002, FR-004 |
| Out-of-band downloads | Every download (rustup installer, oras tarball) SHA-256-verified | FR-004 |
| Default user | `USER 65534:65534` (nobody:nogroup) | FR-006, R4 |
| Tool reachability | cargo, rustc, cc, curl, oras all on `ENV PATH`, runnable by `65534` w/o root | FR-006, R4 |
| Run-time installs | **None** — no package manager or toolchain download at run time | FR-003 |
| Published location | `ghcr.io/<owner>/<repo>/build-image`, referenceable by immutable digest | FR-005 |

**Validation**: `tests/test_build_image_hardening.py` asserts (statically, over `Dockerfile` text): `FROM` line carries `@sha256:`; rustup channel is a concrete `X.Y.Z`, not `stable`; apt installs use `pkg=<ver>` pinning; oras + rustup downloads are followed by a `sha256sum -c`/checksum compare; a `USER 65534` line exists; no `CMD`/`RUN` performs run-time-style installs unguarded.

**Lifecycle**: `authored (Dockerfile)` → `built (BuildKit)` → `published (GHCR, immutable digest surfaced)` → `referenced-by-digest (executor)`. Image-self provenance/attestation is **out of scope** (US2 clarification, FR-005a).

---

## Entity: Build script (`scripts/build-rust.sh`)

Run-time build logic executed by the executor inside the hardened container.

| Attribute | Value / Rule | Trace |
|---|---|---|
| Identity | Runs as `65534:65534`, no privilege escalation, default caps only, requests **no** added caps | FR-007 |
| Root filesystem | Treated read-only; never written | FR-008 |
| Workspace (`/workspace`) | Treated read-only; never written (source is **copied** into scratch) | FR-009 |
| Scratch root | `${BUILD_SCRATCH_DIR:-/tmp}` — single mount; all writes are subdirs beneath it | FR-010, FR-010a, R5 |
| Write targets (exhaustive) | `CARGO_HOME`, cargo `CARGO_TARGET_DIR`, source copy, downloads, oras auth/scratch — all under scratch | FR-010 |
| Run-time installs | Removed (no Step 0, no rustup install, no oras download) | FR-003, FR-011, R7 |
| Required env inputs | `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `COMMIT_SHA` (optional `BUILD_SCRATCH_DIR`) | (existing contract) |
| Stdout markers | `BINARY_SHA256:<64-hex>` and `BINARY_OCI_REF:<ref>`, current format | FR-013, SC-007 |
| Transfer mechanism | oras push of the binary to GHCR, unchanged | FR-014, SC-007 |
| Failure behavior | Non-zero exit + named condition + offending detail (missing tool / out-of-scratch write / exhaustion / egress) | FR-016, FR-016b |
| No partial artifact | Digest computed only over a complete binary; push all-or-nothing; markers only after successful push | FR-016a, FR-016b |

**State transitions** (each arrow aborts with an attributable error on failure — FR-016):
```
preflight (tools present, scratch writable)
  → stage source into scratch
  → cargo build --release (CARGO_HOME/CARGO_TARGET_DIR in scratch)
  → verify binary exists
  → compute SHA-256
  → oras login + push to GHCR
  → emit BINARY_SHA256 / BINARY_OCI_REF markers   ← only reached on full success
```

**Validation**: `tests/test_build_image_hardening.py` asserts the script text contains **no** `apt-get|dnf|yum|apk` install and **no** `sh.rustup.rs`/rustup download; sets `CARGO_HOME`/`RUSTUP_HOME`/`CARGO_TARGET_DIR` under the scratch root; preflights tools via `command -v` with a tool-naming `die`; emits both markers; and emits markers only after the push step.

---

## Entity: Writable scratch mount

The single executor-provided tmpfs — the only writable location.

| Attribute | Value / Rule | Trace |
|---|---|---|
| Kind | Single **tmpfs** mount provided by the executor | FR-010a, Assumptions |
| Path | Executor-mounted (default `/tmp`; overridable via `BUILD_SCRATCH_DIR`) | R5 |
| Contents | `CARGO_HOME` (`.cargo`), `CARGO_TARGET_DIR` (`target`), source copy, downloads, oras auth | FR-010 |
| Minimum size | Conservative floor **≥ 4 GiB** (basis: toolchain caches + downloads + release `target/` + headroom) | FR-018 |
| Exhaustion behavior | Build fails clearly naming scratch exhaustion; no corrupt output | Edge Cases, FR-016 |

**Validation**: documented in README (operator-facing); exhaustion path covered by the script's `die` on compile/link failure (R6).

---

## Entity: Attested OCI artifact

The final `attested-hello` output published to GHCR, consumed by downstream attestation.

| Attribute | Value / Rule | Trace |
|---|---|---|
| Binary | `attested-hello` release build, linux/amd64 | FR-015 |
| Digest | SHA-256 over the **complete** binary; reported via `BINARY_SHA256` and re-verified by the workflow | FR-013, FR-015, FR-016a |
| Transfer | Pushed as an OCI artifact via oras; workflow pulls, verifies, then publishes the final attested artifact | FR-014, FR-015 |
| Integrity | Never partial/corrupt — push completes the verified artifact or fails | FR-016a |

**Note**: This entity and the downstream workflow steps (provenance manifest, attestation bundle, GitHub attestation) are **unchanged** by this feature; they are listed for completeness of the data flow and to anchor the SC-007 compatibility requirement.

---

## Configuration surface (cross-cutting)

| Setting | Owner | Value | Trace |
|---|---|---|---|
| Container user | Executor (global) | `65534:65534` | FR-006, FR-007 |
| Rootfs / workspace mode | Executor (global) | read-only | FR-008, FR-009 |
| Capabilities / no-new-privileges | Executor (global) | defaults; none added | FR-007, FR-012 |
| Network egress | Executor (global) | allowed (sole exception) | FR-012, FR-016b |
| Build image reference | Operator → executor | immutable `…@sha256:<digest>` | FR-017 |
| Scratch size | Operator → executor | ≥ 4 GiB | FR-018 |
| `BUILD_SCRATCH_DIR` | Build script env (optional) | defaults `/tmp` | R5 |
