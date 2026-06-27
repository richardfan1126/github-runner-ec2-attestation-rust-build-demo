# hardened-build-environment Design

Rationale behind [[hardened-build-environment]]. Captures *why* the build image
and build script are shaped the way they are. Distilled from the spec-kit Phase-0
research (R1–R10) for `001-hardened-executor-build`.

## Context

The Remote Executor runs the build container under hardened defaults that the
operator is not expected to relax: rootless `65534:65534`, read-only root
filesystem, read-only `/workspace`, no-new-privileges, default capabilities, and a
single writable tmpfs scratch mount. Network egress is the only assumed exception.
The legacy build script only worked by weakening the executor — it ran as root on
a writable rootfs so it could `apt-get install` the toolchain and download oras at
run time. This design removes that requirement by moving all tool provisioning to
image-build time and confining all run-time writes to scratch.

## Goals / Non-Goals

**Goals:**

- Build succeeds unmodified under the executor's hardened defaults.
- The build image is reproducible and pinned (a supply-chain anchor by
  reproducibility), consumable by immutable digest.
- All run-time writes land in the single scratch mount; nothing else is writable.
- Failures are clear, attributable, and never fall back to a privileged path.

**Non-Goals:**

- Modifying the Remote Executor (separate repo) — it is a fixed dependency.
- Removing the network dependency on GHCR for the oras push.
- Provenance/attestation *of the build image itself* — only the build's output
  artifact is attested (see [[attested-build-workflow]]).
- Cross-platform builds — `linux/amd64` only, matching the toolchain target.

## Decisions

### Invoke the real cargo/rustc directly, keep RUSTUP_HOME read-only (R1)

Install the pinned toolchain at image-build time via rustup, then add
`$RUSTUP_HOME/toolchains/<channel>-x86_64-unknown-linux-gnu/bin` to `PATH` and call
the **real** `cargo`/`rustc` binaries — not the rustup proxy shims. At run time
`CARGO_HOME` and `CARGO_TARGET_DIR` point under scratch; `RUSTUP_HOME` stays at its
read-only image path. The real binaries need a writable `CARGO_HOME` but never
write `RUSTUP_HOME` — only the rustup proxies lock/consult it. Because
`attested-hello` has zero crate dependencies, `cargo build --release` does no
registry/network access and writes only a small amount, all redirected to scratch.

*Rejected:* keeping the proxies with a writable `RUSTUP_HOME` in scratch (would
require seeding the toolchain into scratch per run — slow, defeats "pre-installed",
risks exhaustion); `cargo install`-ing the toolchain into `CARGO_HOME` (not how
rustup lays toolchains out; brittle).

### Pin everything: base by digest, tools to exact versions, downloads by SHA-256 (R2, R3)

Base on `debian:bookworm-slim` pinned by content digest (never a floating tag).
Rust via rustup at an exact stable channel (`1.96.0`, not `stable`), `--profile
minimal`; `gcc`/`libc6-dev`/`curl` as exact `pkg=<version>` apt packages resolved
against the pinned base; oras as a pinned release tarball. Every out-of-band
download (rustup-init, oras tarball) is SHA-256-verified before use. Patch-exact
pins plus checksums are what make two builds from the same image digest identical.

*Rejected:* distro-packaged Rust (`apt-get install rustc`) — lags and isn't
channel-pinnable; Alpine/musl — would change the Rust target and linker story for
no benefit. Debian chosen over Ubuntu for a smaller, more deterministic package set
(either is acceptable if digest-pinned). The concrete digests/versions/checksums
live in the Dockerfile as real verifiable values, re-captured if upstream
re-publishes.

### Rootless usability via world-exec paths, global PATH, explicit homes (R4)

Install the toolchain (`/opt/rust`) and oras (`/usr/local/bin`) world-readable/
executable, set a global `ENV PATH`, and end the Dockerfile with `USER
65534:65534` so the image's default identity matches the executor. Set
`CARGO_HOME`/`RUSTUP_HOME`/`CARGO_TARGET_DIR` explicitly rather than `$HOME`-relative
— UID `65534` has no writable home. Setting `USER` also lets local `docker run`
mirror the executor without `--user`.

### Single scratch mount resolved from an env var with a /tmp default (R5)

`SCRATCH_DIR="${BUILD_SCRATCH_DIR:-/tmp}"`; every write target (`CARGO_HOME`,
`CARGO_TARGET_DIR`, the source copy, oras auth) is a subdirectory beneath it. The
executor mounts its writable tmpfs at `/tmp`, so the default preserves
compatibility while the override keeps the script portable if the mount path moves.
Scratch is a *single* mount with subdirs, not multiple independent mounts.

### Attributable failures via per-step `die`, OS-enforced for stray writes (R6)

Preflight `command -v` each tool and `die "required tool not found: <name>"` on
absence. Write-probe the scratch root up front (the one proactive filesystem
check); rely on the read-only rootfs/workspace to OS-enforce any stray write
(`EROFS`) rather than the script classifying "outside scratch." Append a
scratch-exhaustion message on a failed compile/link write. Compute the digest only
after a successful build and emit success markers only after a successful push, so
no partial artifact is ever advertised.

*Rejected:* a generic `EXIT` trap that classifies errors — less attributable than
per-step `|| die "<specific condition + detail>"`, which also matches the existing
script style.

### Delete all run-time install/download paths (R7)

Remove the legacy "Step 0" package install, the rustup-install branch, and the oras
download entirely — not guard them behind `if missing`. A conditional install is
still a forbidden privileged fallback under hardened defaults, so the code paths
must not exist.

### Image-publish CI surfaces the immutable digest (R8)

`.github/workflows/build-image.yml` builds the Dockerfile for `linux/amd64` via
`docker/build-push-action`, pushes to this repo's GHCR namespace, and writes the
`...@sha256:<digest>` reference to the step summary using the action's typed
`digest` output (more reliable than parsing CLI output). No attestation of the
image itself.

### Conservative ≥4 GiB scratch floor, with a measurement path (R9)

Document a conservative ≥4 GiB writable-scratch floor with its basis (toolchain
caches + downloads + release `target/` + headroom), noting it may be measured down.
Actual peak usage for a zero-dependency build is expected well under the floor, but
4 GiB stays the documented operator requirement until measured.

## Risks / Trade-offs

- **Pinned literals drift.** Digests/versions/checksums are point-in-time and must
  be re-captured if upstream re-publishes a tag; the captured values live in the
  Dockerfile so they fail loudly (`sha256sum -c`) rather than silently drifting.
- **Conservative scratch floor over-provisions.** 4 GiB is deliberately generous
  for a zero-dependency build; accepted to avoid under-provisioning operators,
  with a documented path to tighten it with data.
- **Network egress remains required** for the final GHCR push; removing it is
  explicitly out of scope. A blocked-egress run fails clearly rather than silently.
- **Direct-binary invocation couples to the rustup layout.** Bypassing the proxy
  shims hard-codes the `toolchains/<channel>.../bin` path (overridable via
  `RUST_TOOLCHAIN_BIN`); a rustup layout change would require updating it.
