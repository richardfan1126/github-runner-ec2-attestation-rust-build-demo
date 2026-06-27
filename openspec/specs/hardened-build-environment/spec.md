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

### Requirement: Build Script Depends on the Upstream rust-build Flavor's Toolchain Contract

The build script SHALL depend on a fixed toolchain contract provided by the upstream
`rust-build` flavor (`github-runner-ec2-attestation/flavors/rust-build/`), not on any image
built in this repository. The repository SHALL record this contract — the tools, paths, and
versions the build script assumes — so divergence from the upstream flavor is detectable.

#### Scenario: Contract enumerates the tools, paths, and versions the build assumes

- **WHEN** the toolchain contract is inspected (in the spec and where `scripts/build-rust.sh` declares its defaults)
- **THEN** it states that the execution image must provide `cargo`, `rustc`, a C linker (`cc`), `curl`, and `oras` on the runtime PATH for the unprivileged user `65534:65534`, with `RUSTUP_HOME=/opt/rust` and the real toolchain binaries at `/opt/rust/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin`, and names the upstream `rust-build` flavor as the provider of that image

#### Scenario: Build script assumes the contract without re-installing or re-pinning

- **WHEN** `scripts/build-rust.sh` runs in the flavor's execution container
- **THEN** it relies on the contract's pre-installed tools and paths, performs no run-time install, and this repository builds and publishes no image of its own to satisfy them

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

The repository SHALL document, discoverably in the README (or a top-level doc it prominently
links), how an operator runs this build against the upstream `rust-build` flavor — which
flavor/attestable AMI to select and what the executor must provide — without describing any
in-repo build-image to pin.

#### Scenario: Docs point operators at the upstream flavor and its attestable AMI

- **WHEN** an operator looks for build configuration in the repository docs
- **THEN** they are directed to the upstream `rust-build` flavor (`github-runner-ec2-attestation/flavors/rust-build/`) and its per-flavor, PCR4-bound attestable AMI as the source of the execution image, and the docs do NOT instruct them to pin an in-repo `build-image@sha256:<digest>` reference

#### Scenario: Scratch mount size is documented with its basis

- **WHEN** an operator reads how the executor's writable scratch mount is sized
- **THEN** the docs state that the `rust-build` flavor provisions a 2 GiB exec-enabled tmpfs scratch mount (`CONTAINER_TMPFS_SIZE=2g`, `CONTAINER_TMPFS_EXEC=true`) baked into the PCR4-bound AMI rather than sized by the operator, and explain the basis (toolchain writable home(s), downloaded artifacts, and the release `target/` directory with headroom)

#### Scenario: Docs make clear no operator security changes are needed

- **WHEN** the operator follows the documentation
- **THEN** it is clear that selecting the `rust-build` flavor AMI is the whole setup, that the flavor carries the build's required relaxations from the hardened executor defaults (network mode `none`→`bridge` for the GHCR push and the writable tmpfs scratch `noexec`→`exec` at 2 GiB, recorded in `flavors.lock`), and that the operator changes no executor security setting themselves
