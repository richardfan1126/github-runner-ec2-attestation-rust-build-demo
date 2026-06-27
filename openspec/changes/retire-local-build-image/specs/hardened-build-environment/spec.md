## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Operator Documentation for Executor Configuration

The repository SHALL document, discoverably in the README (or a top-level doc it prominently
links), how an operator runs this build against the upstream `rust-build` flavor — which
flavor/attestable AMI to select and what the executor must provide — without describing any
in-repo build-image to pin.

#### Scenario: Docs point operators at the upstream flavor and its attestable AMI

- **WHEN** an operator looks for build configuration in the repository docs
- **THEN** they are directed to the upstream `rust-build` flavor (`github-runner-ec2-attestation/flavors/rust-build/`) and its per-flavor, PCR4-bound attestable AMI as the source of the execution image, and the docs do NOT instruct them to pin an in-repo `build-image@sha256:<digest>` reference

#### Scenario: Minimum scratch size is documented with its basis

- **WHEN** an operator provisions the executor's writable scratch mount
- **THEN** the docs state a conservative minimum floor of at least 4 GiB sufficient for a Rust release build, explain the basis (toolchain writable home(s), downloaded artifacts, and the release `target/` directory with headroom), and note it may be validated/lowered by measuring actual peak usage

#### Scenario: Docs make clear no security changes are needed

- **WHEN** the operator follows the documentation
- **THEN** it is clear that compatibility comes from the flavor's image and this repo's build script alone, and they can run a successful build without changing any executor security setting other than the already-assumed network egress

## REMOVED Requirements

### Requirement: Build Image Ships All Tools Pre-Installed

**Reason**: The build image is no longer owned by this repository. The upstream `rust-build`
flavor (`github-runner-ec2-attestation/flavors/rust-build/Dockerfile`) defines and pre-installs
the toolchain; this repo's `Dockerfile` was a byte-identical duplicate (comments aside) and is
removed.
**Migration**: The pre-installed-tools guarantee is now expressed as the "Build Script Depends
on the Upstream rust-build Flavor's Toolchain Contract" requirement and provided by the upstream
flavor image baked into the per-flavor attestable AMI.

### Requirement: Build Image Is Reproducible and Pinned

**Reason**: Reproducibility and pinning of the image (digest-pinned base, version-pinned OS
packages, checksum-verified downloads, and CI publishing it by digest) are now the upstream
flavor pipeline's responsibility. This repo's image-publishing workflow
(`.github/workflows/build-image.yml`) and `Dockerfile` are removed, so a "published by this
repo's CI" requirement no longer has a referent.
**Migration**: Rely on the upstream `unify-image-build-producers` pipeline, which builds the
flavor image, derives its amd64 manifest digest, records it in `flavors.lock`, and bakes it
into a PCR4-bound attestable AMI.

### Requirement: Tools Are Usable by the Unprivileged Default User

**Reason**: This was a property of the image this repo built. With the image owned upstream,
the rootless-PATH guarantee for `65534:65534` is the flavor image's responsibility.
**Migration**: Covered by the upstream flavor's hardened contract and surfaced here as part of
the "Build Script Depends on the Upstream rust-build Flavor's Toolchain Contract" requirement
(tools on PATH for `65534:65534`).
