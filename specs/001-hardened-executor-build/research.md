# Phase 0 Research: Hardened-Executor-Compatible Attested Build

**Feature**: `001-hardened-executor-build` | **Date**: 2026-06-15

This document resolves the implementation unknowns implied by the spec. The spec itself has **no open `NEEDS CLARIFICATION`** (all checklist items resolved in `checklists/hardening.md`), so the work here is best-practice / mechanism selection, not requirements clarification.

---

## R1. Relocating cargo/rustc writable state off a read-only toolchain

**Decision**: Install the pinned toolchain at image-build time with `rustup`, then **invoke the toolchain binaries directly** (add `$RUSTUP_HOME/toolchains/<channel>-x86_64-unknown-linux-gnu/bin` to `PATH`), **not** through the rustup proxy shims. At run time set `CARGO_HOME` and `CARGO_TARGET_DIR` to subdirectories under the scratch mount. `RUSTUP_HOME` stays in the read-only image and is never written to at run time because the real `cargo`/`rustc` binaries (unlike the rustup proxies) do not touch `RUSTUP_HOME`.

**Rationale**:
- The real `cargo`/`rustc` need a *writable* `CARGO_HOME` (for its caches/locks) but do **not** need a writable `RUSTUP_HOME`. The rustup *proxy* shims are what consult/lock `RUSTUP_HOME`; bypassing them removes the only reason `RUSTUP_HOME` would need to be writable.
- `attested-hello` has **zero crate dependencies** (`Cargo.lock` lists only the package itself), so `cargo build --release` performs **no registry/network access** and writes only a small amount under `CARGO_HOME` + the `target/` dir — both redirected to scratch.
- Keeps the pre-installed toolchain immutable and reproducible (FR-004, SC-005) while satisfying scratch-only writes (FR-010, SC-003).

**Alternatives considered**:
- *Keep rustup proxies + writable `RUSTUP_HOME` in scratch*: would require copying/seeding the toolchain into scratch at run time (slow, defeats "pre-installed", risks scratch exhaustion). Rejected.
- *Vendor/`cargo install` the toolchain into `CARGO_HOME` only*: not how rustup lays out toolchains; brittle. Rejected.

---

## R2. Base image selection and digest pinning (FR-004a)

**Decision**: Base on `debian:bookworm-slim`, **pinned by content digest** (`debian:bookworm-slim@sha256:<digest>`). Capture the digest at implementation time via `docker buildx imagetools inspect debian:bookworm-slim` (or `docker inspect --format='{{index .RepoDigests 0}}'` after pull) and hard-code it in the `Dockerfile`.

**Rationale**: Debian slim has a stable, well-known `apt` version-pinning story (`pkg=<exact-version>`) needed for `cc`/`curl` (FR-004), a small footprint, and glibc (matching the current `x86_64-unknown-linux-gnu` toolchain). Digest pinning makes the OS layer reproducible alongside the four named tools.

**Alternatives considered**:
- `ubuntu:24.04` (what the legacy script assumed): equally workable; Debian slim chosen for a smaller, more deterministic package set. Either is acceptable if digest-pinned — implementer may keep Ubuntu if preferred, but **must** pin by digest.
- Alpine/musl: would change the Rust target to `musl` and the linker story; unnecessary scope. Rejected.

**Note**: The exact `sha256:` digest is an implementation value captured in the Dockerfile, not fixed in this plan (it changes as upstream re-publishes the tag). The *requirement* is that a digest — never a floating tag — appears in the `FROM` line.

---

## R3. Pinning the four tools (FR-004)

**Decision**:
- **Rust toolchain** — `rustup` installed via the official installer script, itself downloaded then **SHA-256-verified** before execution, run with `--default-toolchain <exact>` (e.g. `1.96.0` — current stable as of 2026-05 — never `stable`) and `--profile minimal`. Implementer selects the current stable patch release and records it.
- **C compiler/linker + curl** — installed as exact, version-pinned Debian packages: `apt-get install -y --no-install-recommends gcc=<ver> libc6-dev=<ver> curl=<ver>` (resolve exact versions against the pinned base image's `apt` index at build time; `build-essential` may be substituted but pin whatever is installed).
- **oras CLI** — download `oras_1.3.2_linux_amd64.tar.gz` from the GitHub release, **verify against the published SHA-256** (from the release `*_checksums.txt`) before `tar -x`, install the binary to a PATH location.
- **Any other out-of-band download** (the rustup installer above) — SHA-256-verified.

**Rationale**: Directly satisfies FR-004's per-tool pinning matrix and the "every out-of-band download SHA-256-verified" rule. Pinning patch-exact versions + verifying checksums is what makes two builds from the same image digest identical (SC-005).

**Alternatives considered**: Using distro-packaged Rust (`apt-get install rustc`) — version lags and isn't channel-pinnable the way the spec wants; rejected in favor of rustup with an exact channel.

**Mechanism note**: Concrete version strings and checksums are captured *in the Dockerfile* at implementation time (they must be real, verifiable values). The plan fixes the *method*, not the literals.

---

## R4. Rootless `65534:65534` usability of all tools (FR-006, FR-007)

**Decision**: Install toolchain + oras into world-readable/executable locations (e.g. toolchain under `/opt/rust`, oras at `/usr/local/bin/oras`), set a global `PATH` in the image (via `ENV PATH=...`) that includes the toolchain `bin` and `/usr/local/bin`, and add a final `USER 65534:65534` line so the image's default identity matches the executor. Do **not** rely on a home directory: set `CARGO_HOME`/`RUSTUP_HOME`/`CARGO_TARGET_DIR` explicitly (R1) rather than `$HOME`-relative defaults, since `65534` has no writable home.

**Rationale**: The executor runs the container as `65534:65534` with `no-new-privileges`; every tool must be on PATH and executable by that UID with no root step (FR-006). Setting `USER` in the image also lets local `docker run` validation mirror the executor without `--user` (quickstart).

**Alternatives considered**: Creating a named user with a home dir — unnecessary; the executor pins the numeric UID and the build never needs `$HOME`. Rejected to avoid divergence from the executor's actual identity.

---

## R5. Locating the writable scratch mount (FR-010, FR-010a)

**Decision**: The build script resolves the scratch root from an env var with a safe default: `SCRATCH_DIR="${BUILD_SCRATCH_DIR:-/tmp}"`. All write targets become subdirectories beneath it — `${SCRATCH_DIR}/.cargo` (`CARGO_HOME`), `${SCRATCH_DIR}/target` (`CARGO_TARGET_DIR`), `${SCRATCH_DIR}/rust-project` (source copy), `${SCRATCH_DIR}/oras-auth.json`, `${SCRATCH_DIR}/oras` downloads. The script `mkdir -p`s these under the single mount and writes nowhere else.

**Rationale**: FR-010a fixes scratch as a *single* tmpfs with subdirectories. The legacy script already treated `/tmp` as the sole writable location, and the executor mounts its writable tmpfs there; defaulting to `/tmp` preserves compatibility while the env override keeps the script portable if the executor changes the mount path.

**Alternatives considered**: Hard-coding `/tmp` only — works today but is brittle if the executor relocates scratch. The env-with-default keeps the default behavior while removing the hard dependency. Multiple independent mounts — explicitly contradicted by FR-010a. Rejected.

---

## R6. Attributable failure on missing tool / out-of-scratch write / scratch exhaustion (FR-016, FR-016a, FR-016b)

**Decision**:
- **Preflight tool check**: at start, verify each required tool resolves on PATH (`command -v cargo rustc cc curl oras`); on absence, `die "required tool not found: <name>"` with non-zero exit. Names the specific missing tool (FR-016).
- **Scratch-only writes**: rely on the read-only rootfs/workspace to *enforce* (a stray write fails at the OS level), and additionally validate the scratch root is writable up front (`: > "${SCRATCH_DIR}/.write-probe"` else `die` naming the path and that it is the scratch mount). Any cargo/link write that hits scratch-exhaustion surfaces cargo's own `No space left on device`; the wrapper appends a `die "writable scratch mount exhausted"` on non-zero compile/link exit so the *condition* is named (FR-016 scratch case).
- **No partial artifact**: compute the digest **only after** the binary exists and the build exited 0; push via oras as a single all-or-nothing operation; on any earlier non-zero step the `set -euo pipefail` + `die` aborts *before* emitting success markers, so no corrupt/partial artifact is produced or pushed (FR-016a).
- **Blocked egress**: the oras `login`/`push` failure path `die`s with a message identifying the GHCR push/egress failure and exits non-zero, and the success markers are only printed *after* a successful push — so a blocked-egress run never emits markers for an unpublished artifact (FR-016b).

**Rationale**: Turns the spec's "clear, attributable error" standard (non-zero exit + named condition + offending detail) into concrete shell mechanisms, applied uniformly across the missing-tool, out-of-scratch-write, exhaustion, and egress cases.

**Alternatives considered**: Trapping `EXIT` to classify errors generically — less attributable (can't name the specific condition/detail cleanly). Per-step explicit `|| die "<specific>"` is clearer and matches the existing script style. Kept.

---

## R7. Removing run-time installs (FR-003, FR-011)

**Decision**: Delete the legacy "Step 0: Install required system dependencies" block and the rustup-install branch (Step 2) entirely. The script assumes `cargo`, `rustc`, `cc`, `curl`, `oras` are already present (provided by the image) and **never** invokes `apt-get`/`dnf`/`yum`/`apk` or `sh.rustup.rs`. The `download()` helper is retained only for the (already-present-in-image) oras case is removed too — oras ships in the image, so no run-time download remains; `curl` stays available purely for any diagnostics but is not used to fetch tools.

**Rationale**: FR-011/FR-003/SC-002 require zero run-time installs and no toolchain download. With everything pre-installed, the download/install code paths are dead and must be removed so the script *cannot* attempt a privileged fallback (FR-016 "no privileged fallback").

**Alternatives considered**: Guarding installs behind `if missing` (legacy behavior) — rejected; the requirement is removal, and a conditional install is still a forbidden fallback under hardened defaults.

---

## R8. Image publish CI workflow (FR-005, FR-005a)

**Decision**: New `.github/workflows/build-image.yml` triggered on `workflow_dispatch` (and optionally on push to paths `Dockerfile`/`scripts/build-rust.sh`). Steps: checkout → `docker/login-action` to `ghcr.io` with `GITHUB_TOKEN` → `docker/build-push-action` building the `Dockerfile` for `linux/amd64`, pushing to `ghcr.io/${{ github.repository }}/build-image` with a tag, **and capturing the pushed image digest** (`outputs.digest` from build-push-action) → write the immutable `...@sha256:<digest>` reference to `$GITHUB_STEP_SUMMARY`. No provenance/attestation of the image itself (explicitly out of scope per US2 clarification / FR-005a).

**Rationale**: FR-005a requires a CI workflow *in this repo* that builds from the checked-in Dockerfile, publishes to this repo's GHCR namespace, and surfaces the resulting immutable digest for operators to pin (FR-017). Using `docker/build-push-action`'s `digest` output is the standard, reliable way to obtain it.

**Alternatives considered**: Plain `docker build`/`docker push` + parsing CLI output for the digest — works but more brittle than the action's typed `digest` output. Kept the action.

---

## R9. Documented scratch floor (FR-018)

**Decision**: Document a **conservative ≥4 GiB** writable-scratch floor in the README, stating the basis (toolchain writable home/caches + downloaded artifacts + the release `target/` dir + headroom) and noting it MAY be validated/lowered by measuring peak scratch usage of a real release build (quickstart shows the measurement). Given `attested-hello` has zero dependencies, *actual* peak usage is expected to be well under the floor; the 4 GiB stays as the documented operator requirement until measured down.

**Rationale**: Matches FR-018 verbatim (conservative floor, stated basis, validation method) and the Assumptions. Avoids under-provisioning operators while leaving room to tighten with data.

---

## R10. Captured pinned literals (T001)

**Captured**: 2026-06-15 (Phase 1 / Setup task T001). These are the exact, verifiable values the `Dockerfile` (T009) must hard-code. Each was resolved against the pinned base / official release artifact at capture time; re-capture before a fresh build if upstream re-publishes.

| Literal | Value | Source / verification command |
|---------|-------|-------------------------------|
| `debian:bookworm-slim` content digest (multi-arch index) | `sha256:96e378d7e6531ac9a15ad505478fcc2e69f371b10f5cdf87857c4b8188404716` | `docker buildx imagetools inspect debian:bookworm-slim` → top-level `Digest:` |
| Rust stable channel (exact `X.Y.Z`, never `stable`) | `1.96.0` | `curl -fsS https://static.rust-lang.org/dist/channel-rust-stable.toml` → `[pkg.rust] version = "1.96.0 (ac68faa20 2026-05-25)"` |
| `gcc` apt version (meta-package; registers `/usr/bin/cc` alternative — see T009) | `4:12.2.0-3` | `apt-cache policy gcc` inside the pinned base |
| `libc6-dev` apt version | `2.36-9+deb12u14` | `apt-cache policy libc6-dev` inside the pinned base |
| `curl` apt version | `7.88.1-10+deb12u14` | `apt-cache policy curl` inside the pinned base |
| `oras_1.3.2_linux_amd64.tar.gz` SHA-256 | `9229ccc6d17bb282039ad4a69abb16dcb887a5bce567c075d731d9b3c7ad8eaf` | `oras_1.3.2_checksums.txt` from the `v1.3.2` GitHub release |
| `rustup-init` (x86_64-unknown-linux-gnu) SHA-256 | `4acc9acc76d5079515b46346a485974457b5a79893cfb01112423c89aeb5aa10` | `https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init.sha256` |

**Notes for T009**:
- The apt versions were resolved against the **pinned** base (`debian@sha256:96e378…`), so they install cleanly with `pkg=<ver>` against that exact image's apt index. If the base digest is re-captured, re-resolve these three versions.
- Install the **`gcc` meta-package** (version `4:12.2.0-3`), not a bare `gcc-12` — its postinst registers the `/usr/bin/cc` update-alternatives entry that both T005's preflight and cargo's link step require (R4).
- The `rustup-init.sha256` file is published as ``<sha>  *./rustup-init``; verify with a `sha256sum -c` after rewriting the path, or compare the bare hash above.

---

## Resolved unknowns summary

| # | Unknown | Resolution |
|---|---------|------------|
| R1 | Read-only toolchain + writable cargo state | Invoke real binaries directly; `CARGO_HOME`/`CARGO_TARGET_DIR` in scratch; `RUSTUP_HOME` read-only |
| R2 | Base image + digest pin | `debian:bookworm-slim@sha256:<captured>` |
| R3 | Per-tool pinning + checksums | rustup exact channel; apt `pkg=<ver>` for cc/curl; oras tarball + SHA-256; all downloads verified |
| R4 | Rootless `65534` usability | World-exec install paths, global `ENV PATH`, `USER 65534:65534`, no `$HOME` reliance |
| R5 | Scratch mount location | `${BUILD_SCRATCH_DIR:-/tmp}` single mount, subdirs beneath |
| R6 | Attributable failures | Preflight `command -v`; write-probe; `|| die "<condition+detail>"`; markers only after successful push |
| R7 | Remove run-time installs | Delete Step 0 + rustup-install + oras download paths entirely |
| R8 | Image publish CI | `build-image.yml` via build-push-action, digest → step summary |
| R9 | Scratch floor | ≥4 GiB documented with basis + measurement method |
| R10 | Pinned literals (T001) | Captured: base digest, Rust 1.96.0, gcc/libc6-dev/curl apt versions, oras + rustup-init SHA-256 |

**Output**: All implementation unknowns resolved. No blocking `NEEDS CLARIFICATION` remain. Proceed to Phase 1.
