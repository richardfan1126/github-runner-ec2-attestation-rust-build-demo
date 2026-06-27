# hardened-build-environment Specification

## Purpose

Define the Rust project and the build script that compile the `attested-hello`
binary and transfer it to GHCR — both running unmodified under the Remote
Executor's hardened, default container-security posture (rootless `65534:65534`,
read-only root filesystem, read-only workspace, no privilege escalation, default
capabilities, a single writable tmpfs scratch mount, with outbound network egress
as the only assumed exception). The build environment (execution container image)
is provided by the upstream `rust-build` flavor
(`github-runner-ec2-attestation/flavors/rust-build/`), baked into a per-flavor
PCR4-bound attestable AMI. This capability documents the toolchain contract the
build script depends on and the operator documentation needed to select and run
the upstream flavor.

This capability covers everything that runs *inside* the execution container. The
GitHub Actions orchestration around it is specified in
[[attested-build-workflow]]; the attested-channel client is specified in
[[attested-executor-caller]].

## Requirements

### Requirement: Rust Project Structure

The Rust project SHALL be a minimal, self-contained project that compiles into
the `attested-hello` binary artifact.

#### Scenario: Project defines the attested-hello binary target

- **WHEN** the repository's `rust-project/` is inspected
- **THEN** it contains a valid `Cargo.toml` declaring a binary target named `attested-hello` and a `src/main.rs` that compiles into an executable

#### Scenario: Release build produces a single binary

- **WHEN** the project is compiled with `cargo build --release`
- **THEN** it produces a single binary artifact at `target/release/attested-hello`

#### Scenario: Binary reports version and build timestamp

- **WHEN** the `attested-hello` binary is executed
- **THEN** it prints a version string and a build timestamp to stdout

### Requirement: Build Image Ships All Tools Pre-Installed

The repository SHALL contain a Dockerfile defining a purpose-built build image
that pre-installs every tool the build needs, so the build performs no run-time
software installation.

#### Scenario: Required toolchain is present without installation

- **WHEN** the image is built from the checked-in Dockerfile and inspected
- **THEN** the stable Rust toolchain (cargo and rustc), a C compiler/linker capable of the final link step, curl, and the oras CLI are all present and executable without installing anything further

#### Scenario: No run-time install is needed or attempted

- **WHEN** the build runs from this image
- **THEN** every tool it requires is already present before the container starts and no package-manager invocation or toolchain download occurs at run time

### Requirement: Build Image Is Reproducible and Pinned

The build image SHALL be reproducible: its base image, its OS packages, and its
out-of-band tool downloads are all pinned, and the published image is referenceable
by an immutable digest.

#### Scenario: Base image and tools are version-pinned

- **WHEN** the Dockerfile is inspected
- **THEN** the base image is pinned by content digest (not a floating tag), the Rust toolchain is pinned to an exact stable channel (e.g. `1.96.0`, not `stable`), the C compiler/linker and curl are pinned distro packages (`pkg=<exact version>`), and the oras CLI is a pinned-version release

#### Scenario: Out-of-band downloads are checksum-verified

- **WHEN** the image build fetches the rustup installer or the oras release tarball
- **THEN** each download is verified against a known SHA-256 checksum before use

#### Scenario: Image is published by CI and referenceable by digest

- **WHEN** the in-repo image-publishing CI workflow runs
- **THEN** it builds the image from the checked-in Dockerfile, publishes it to this repository's GHCR namespace, and surfaces the resulting immutable image digest so consumers can pin to it

### Requirement: Tools Are Usable by the Unprivileged Default User

All pre-installed tools SHALL be invocable by the executor's unprivileged default
user (`65534:65534`, nobody:nogroup) with no root required and reachable on the
runtime PATH.

#### Scenario: Toolchain runs rootless

- **WHEN** the image runs as UID:GID `65534:65534`
- **THEN** every required tool (cargo, rustc, the C linker, curl, oras) is on the runtime PATH and usable without root

### Requirement: Build Writes Only Under the Scratch Mount

The build SHALL treat the container root filesystem and the workspace mount as
read-only, and SHALL perform every write under the executor-provided writable
tmpfs scratch mount only.

#### Scenario: All write targets resolve under scratch

- **WHEN** the build runs
- **THEN** the staged source copy, `CARGO_HOME`, `CARGO_TARGET_DIR`, downloaded artifacts, and the oras auth/credential file are all created as subdirectories beneath the single writable scratch mount, and nothing is written to the read-only root filesystem or the read-only workspace

#### Scenario: RUSTUP_HOME stays read-only

- **WHEN** the build invokes the real `cargo`/`rustc` binaries directly (not the rustup proxy shims)
- **THEN** `RUSTUP_HOME` remains at its read-only image path and is never written at run time

#### Scenario: Source is staged from the read-only workspace into scratch

- **WHEN** the build begins
- **THEN** it copies the Rust project source from the read-only `/workspace/rust-project/` into a writable scratch subdirectory before compiling, excluding any `target/` directory

### Requirement: Build Runs Under Hardened Defaults Without Relaxation

The build SHALL succeed as the unprivileged default user with no privilege
escalation and only the default capability set, and SHALL NOT require the operator
to change any executor security setting.

#### Scenario: No added capabilities or privilege escalation required

- **WHEN** the build (compile, link, digest, oras push) executes
- **THEN** it requires no Linux capabilities beyond the default set, requests no privilege escalation, and never attempts to install software or write outside scratch

#### Scenario: No executor security setting must change

- **WHEN** an operator runs the executor on its default hardened configuration
- **THEN** the build succeeds with zero changes to the container user, root-filesystem mode, workspace mode, capabilities, or no-new-privileges settings, network egress being the single assumed pre-existing exception

### Requirement: Build Requires Forwarded Environment Variables

The build script SHALL require `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and the commit
SHA (forwarded into the execution container) and SHALL fail clearly if any is
missing.

#### Scenario: Missing required env var fails the build

- **WHEN** any of `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, or `COMMIT_SHA` is not set in the execution container
- **THEN** the build script exits non-zero with a descriptive error naming the missing variable

### Requirement: Build Produces and Transfers the Binary via a Temporary GHCR Package

On success the build SHALL compute the binary's SHA-256 digest, push it to GHCR as
a temporary OCI package using oras, and emit the stdout markers the workflow
consumes.

#### Scenario: Build emits success markers only after a successful push

- **WHEN** the build completes a successful `oras push`
- **THEN** it prints `BINARY_SHA256:<64-char-hex>` and `BINARY_OCI_REF:<reference>` to stdout in their existing format, and the reported digest matches the verified binary

#### Scenario: Temporary package path and source annotation

- **WHEN** the build pushes the binary
- **THEN** it authenticates to GHCR with `GITHUB_TOKEN` via oras, pushes to `ghcr.io/<GITHUB_REPOSITORY>/tmp-build:<tag>` where `<tag>` derives from the short commit SHA plus a unique suffix, and includes the `org.opencontainers.image.source` annotation pointing at the repository so cleanup permissions resolve

### Requirement: Build Fails Clearly Without Privileged Fallback

The build SHALL fail with a clear, attributable, non-zero error — and SHALL NOT
silently continue, publish a partial artifact, or attempt a privileged fallback —
if a required tool is missing, a write is attempted outside scratch, scratch is
exhausted, or egress is blocked.

#### Scenario: Missing tool is named

- **WHEN** a required tool is absent from the image at run time
- **THEN** the script preflight fails non-zero naming the specific missing tool, and never installs it or escalates

#### Scenario: Write outside scratch is OS-denied

- **WHEN** any build step attempts to write outside the scratch mount (to the read-only rootfs or workspace)
- **THEN** the kernel denies it (`EROFS`), the build fails non-zero surfacing the failing command's error including the offending path, and the script does not retry with privilege; the script's one proactive check write-probes the scratch root up front and, if scratch is unwritable, fails non-zero naming the scratch path

#### Scenario: Scratch exhaustion is surfaced

- **WHEN** a compile/link write fails for lack of space
- **THEN** the build fails non-zero indicating the writable scratch mount was filled

#### Scenario: No partial or corrupt artifact is published

- **WHEN** a mid-build failure occurs (scratch exhaustion, compile error, link failure, or interrupted push)
- **THEN** the build aborts without producing or pushing a corrupt or partial OCI artifact — the digest is computed only over a fully built binary and the oras push either completes the verified artifact or fails

#### Scenario: Blocked egress fails the push clearly

- **WHEN** outbound network egress is blocked
- **THEN** the final GHCR push fails non-zero with a message identifying the push/egress failure, and no success markers are emitted for an unpublished artifact

### Requirement: Operator Documentation for Executor Configuration

The repository SHALL document, discoverably in the README (or a top-level doc it
prominently links), exactly how to point the executor at this build image and what
the executor must provide.

#### Scenario: Image reference is documented as an immutable digest

- **WHEN** an operator looks for build configuration in the repository docs
- **THEN** they find the exact container image reference to configure, expressed as an immutable digest (`...@sha256:<digest>`) rather than a floating tag

#### Scenario: Minimum scratch size is documented with its basis

- **WHEN** an operator provisions the executor's writable scratch mount
- **THEN** the docs state a conservative minimum floor of at least 4 GiB sufficient for a Rust release build, explain the basis (toolchain writable home(s), downloaded artifacts, and the release `target/` directory with headroom), and note it may be validated/lowered by measuring actual peak usage

#### Scenario: Docs make clear no security changes are needed

- **WHEN** the operator follows the documentation
- **THEN** it is clear that compatibility comes from the image and build script alone, and they can run a successful build without changing any executor security setting other than the already-assumed network egress
