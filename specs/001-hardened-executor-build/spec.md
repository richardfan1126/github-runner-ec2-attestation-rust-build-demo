# Feature Specification: Hardened-Executor-Compatible Attested Build

**Feature Branch**: `001-hardened-executor-build`

**Created**: 2026-06-14

**Status**: Draft

**Input**: User description: "Make the attested Rust build pipeline run on a Remote Executor that uses its hardened, default container-security configuration, instead of requiring the operator to relax the executor's security posture."

**Scope**: This feature delivers two artifacts in *this* repository — the purpose-built build image and the changes to the build script that let it run under hardened defaults. The Remote Executor (separate repo `github-runner-ec2-attestation`) is the external environment this build must fit into; it is treated as a fixed dependency and is **not** modified by this feature.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build succeeds under the executor's hardened defaults (Priority: P1)

An operator runs the Remote Executor with its out-of-the-box hardened security posture: the execution container runs as an unprivileged user (no root), the root filesystem is read-only, the workspace is mounted read-only, privilege escalation is disabled, only the default capability set is granted, and the only writable space is a small scratch mount. The operator points the executor at the project's purpose-built build image and dispatches the build workflow. The pipeline compiles the `attested-hello` binary, verifies its digest, and publishes the attested OCI artifact to GHCR — without the operator changing any of the executor's security settings (network egress being the single pre-existing exception).

**Why this priority**: This is the entire purpose of the feature. Today the demo only works if the operator weakens the executor (allowing root and a writable root filesystem so the build can `apt-get install` its tools). Removing that requirement is the core value and the minimum viable outcome.

**Independent Test**: Configure an executor with the hardened defaults unchanged, point it at the published build image, dispatch the workflow, and confirm an attested artifact lands in GHCR with the expected digest — all with no edits to the executor's user, rootfs, workspace mode, capability, or no-new-privileges settings.

**Acceptance Scenarios**:

1. **Given** an executor on its default hardened configuration and the build image in place, **When** the workflow is dispatched, **Then** the build completes successfully and pushes the attested OCI artifact to GHCR.
2. **Given** the build runs as the unprivileged default user with a read-only root filesystem and read-only workspace, **When** the build executes, **Then** it writes only under the writable scratch mount and never attempts to install software, escalate privileges, or write outside scratch.
3. **Given** a successful build, **When** the workflow parses the build's output, **Then** the `BINARY_SHA256` and `BINARY_OCI_REF` stdout markers are present and the reported digest matches the verified binary.

---

### User Story 2 - Reproducible, pinned build image as a supply-chain anchor (Priority: P2)

A maintainer needs the build environment itself to be trustworthy and reproducible, consistent with the project's supply-chain-attestation goals. A purpose-built container image is defined by a Dockerfile checked into this repository and published to GHCR. The image ships every tool the build needs already installed — the Rust toolchain (stable cargo/rustc), a C compiler/linker for the final link step, curl, and the oras CLI — with tool versions pinned and the image consumable by digest.

**Why this priority**: Pre-installing the toolchain is what makes the hardened-defaults build (P1) possible, and pinning/digest-referencing is what keeps the build environment reproducible and attestable. It is foundational to P1 but is called out separately because the image is an independently versioned, independently verifiable deliverable.

> **Clarification (supply-chain anchor scope)**: The image serves as a supply-chain anchor through *reproducibility* — a digest-pinned base image (FR-004a), pinned tool versions (FR-004), and an immutable published digest (FR-005, FR-005a). Build-time *provenance/attestation of the image itself is out of scope*; only the build's output artifact is attested. The image-publishing CI surfaces the final image digest so consumers can pin to it.

**Independent Test**: Build the image from the checked-in Dockerfile, then inside it confirm each required tool is present at its pinned version and runnable by the unprivileged default user; confirm the published image can be referenced by an immutable digest.

**Acceptance Scenarios**:

1. **Given** the checked-in Dockerfile, **When** the image is built, **Then** cargo/rustc (stable), a working C linker, curl, and the oras CLI are all present and executable without installing anything further.
2. **Given** the published image, **When** it is referenced for a build, **Then** it can be pinned to an immutable digest, and its tool versions are fixed rather than floating.
3. **Given** the image runs as the unprivileged default user, **When** the toolchain is invoked, **Then** every required tool is on the runtime PATH and usable without root.

---

### User Story 3 - Operator knows exactly how to point the executor at the image (Priority: P3)

Whoever operates the executor needs unambiguous guidance: which container image to configure as the build image, and the minimum size of the writable scratch mount the executor must provide so a release build fits. This is documented in the repository so the operator can configure the executor correctly on the first try.

**Why this priority**: Without this, an operator cannot realize the P1 outcome even when the image and script are correct. It is lower priority than the working build itself, but required for the feature to be usable by someone who did not build it.

**Independent Test**: A new operator, reading only the repository documentation, can identify the exact image reference to configure and the minimum scratch size to allocate, then successfully run a build.

**Acceptance Scenarios**:

1. **Given** the repository documentation, **When** an operator looks for build configuration, **Then** they find the exact container image reference to point the executor at.
2. **Given** the repository documentation, **When** an operator provisions the executor's writable scratch mount, **Then** they find a stated minimum size sufficient for a Rust release build.
3. **Given** the documentation, **When** the operator follows it, **Then** they can run a successful build without changing any executor security setting other than the already-assumed network egress.

---

### Edge Cases

- **Scratch mount too small**: If the writable scratch space is smaller than the documented minimum, a release build may exhaust it mid-compile. The build must fail clearly (surfacing that scratch was exhausted) rather than corrupting output or silently producing a wrong binary.
- **Read-only enforcement**: If any build step attempts to write outside the scratch mount (e.g., to the root filesystem or the read-only workspace), it must fail fast with a clear error rather than appearing to succeed.
- **Stale/unpinned image**: If the executor is pointed at a floating tag instead of the pinned digest, the build still functions but loses reproducibility; documentation must steer operators to the pinned reference.
- **Network egress unavailable**: The oras push to GHCR requires outbound network. If egress is blocked, the push step fails with a clear error; this is the one documented external dependency and is out of scope to remove.
- **Toolchain unexpectedly missing**: If a required tool is absent from the image at runtime, the build must fail with a clear message and must NOT fall back to installing it at run time (that fallback is being removed).

## Requirements *(mandatory)*

### Functional Requirements

#### Build image

- **FR-001**: The repository MUST contain a Dockerfile that defines a purpose-built build image for this pipeline.
- **FR-002**: The build image MUST come with all build-time tools pre-installed: the stable Rust toolchain (cargo and rustc), a C compiler/linker capable of performing the final link step, curl, and the oras CLI.
- **FR-003**: The build image MUST require no run-time software installation — every tool needed by the build is present before the container starts.
- **FR-004**: The pre-installed tool versions MUST be pinned (fixed, not floating) so the image is reproducible. Per tool: the **Rust toolchain** MUST be installed via rustup pinned to an exact stable channel (e.g., `1.81.0`, not `stable`); the **C compiler/linker** and **curl** MUST be installed as exact, version-pinned distro packages (`pkg=<exact version>`); the **oras CLI** MUST be fetched as a pinned-version release tarball whose download is verified against a known **SHA-256 checksum**. Any out-of-band download used during image build (including the rustup installer) MUST likewise be SHA-256-verified.
- **FR-004a**: The Dockerfile's base image MUST be pinned by content digest (not by a floating tag), so the OS layer is reproducible alongside the four named tools.
- **FR-005**: The build image MUST be published to GHCR and MUST be referenceable by an immutable digest.
- **FR-005a**: A CI workflow in *this* repository MUST build the image from the checked-in Dockerfile and publish it to this repository's GHCR namespace; after publishing, the workflow MUST produce (surface/record) the resulting immutable image digest. Provenance/attestation of the build image itself is NOT required — only the build's output artifact is attested (see US2 clarification).
- **FR-006**: All pre-installed tools MUST be invocable by the unprivileged default user (no root required) and reachable on the runtime PATH. The executor's hardened default user is UID:GID `65534:65534` (nobody:nogroup); the image and build script MUST function under that identity.

#### Build execution under hardened defaults

- **FR-007**: The build MUST succeed when running as the executor's unprivileged default user (non-root, UID:GID `65534:65534`) with no privilege escalation and only the default capability set. The build steps (compile, link, digest, oras push) require **no added Linux capabilities** beyond that default set and in fact rely on none of them; the build MUST NOT request or assume any capability the executor does not grant by default.
- **FR-008**: The build MUST treat the container root filesystem as read-only and MUST NOT write to it.
- **FR-009**: The build MUST treat the workspace mount as read-only and MUST NOT write to it.
- **FR-010**: The build MUST perform all writes — including the Rust toolchain's writable home(s) (`CARGO_HOME`/`RUSTUP_HOME`), the cargo build target directory (`CARGO_TARGET_DIR`), downloaded artifacts, and credential/scratch files — under the executor-provided writable scratch mount only. This enumeration is intended to be **exhaustive** of the build's write targets: together with FR-008 (read-only rootfs) and FR-009 (read-only workspace), every write the build performs lands in scratch and nowhere else.
- **FR-010a**: The writable scratch mount is a **single tmpfs mount** provided by the executor; the build organizes the targets in FR-010 as subdirectories beneath it rather than expecting multiple independent mounts.
- **FR-011**: The build script MUST remove the run-time package-install step (formerly "Step 0") and MUST NOT attempt to install or update any system package at run time.
- **FR-012**: The build MUST NOT require the operator to change any executor security setting — container user, root filesystem mode, workspace mode, capabilities, or no-new-privileges — to succeed. Outbound network egress is the single assumed pre-existing exception.
- **FR-013**: The build MUST continue to emit the existing stdout markers `BINARY_SHA256:<digest>` and `BINARY_OCI_REF:<reference>` in their current format.
- **FR-014**: The build MUST continue to transfer the produced binary to GHCR using the existing oras-push mechanism.
- **FR-015**: On success the build MUST produce the `attested-hello` release binary, compute its SHA-256 digest, and push it as an OCI artifact such that the workflow can verify the digest and publish the final attested artifact.
- **FR-016**: If a required tool is missing or a write is attempted outside the scratch mount, the build MUST fail with a clear, attributable error rather than silently continuing or attempting a privileged fallback. A "clear, attributable error" MUST: (a) cause a non-zero exit so the workflow fails, and (b) emit a message naming **which** condition occurred and the offending detail — for a missing tool, the tool name (e.g., `oras`); for a denied write, the target path and that it lies outside scratch; for scratch exhaustion, that the writable scratch mount was filled. This standard applies uniformly to the missing-tool and out-of-scratch-write cases.
- **FR-016a**: On any mid-build failure (e.g., scratch exhaustion, compile error, link failure, or interrupted push), the build MUST abort without producing or pushing a corrupt or partial OCI artifact. The digest is computed only over a fully built binary, and the oras push either completes the verified artifact or fails — no partially written artifact is published to GHCR.
- **FR-016b**: If outbound network egress is blocked, the final GHCR push MUST fail with a clear, attributable error (non-zero exit, message identifying the push/egress failure) and MUST NOT emit success markers for an unpublished artifact. Removing this network dependency is explicitly out of scope; the requirement is only that the failure is surfaced clearly.

#### Operator configuration & documentation

- **FR-017**: The repository MUST document the exact container image reference an operator points the executor at as the build image, expressed as an **immutable digest** (`...@sha256:<digest>`), not a floating tag — consistent with FR-005 and the stale-tag edge case.
- **FR-018**: The repository MUST document the minimum writable scratch-mount size required for a successful Rust release build, framed as an expectation the executor must satisfy. The documented minimum MUST be a conservative floor of at least 4 GiB. The documentation MUST state the **basis** for this floor (the sum of the toolchain's writable home(s), downloaded artifacts, and the release `target/` directory with headroom) and note that it MAY be validated/lowered by measuring actual peak scratch usage of a release build.
- **FR-019**: The documentation MUST make clear that compatibility is achieved through the image and build script alone, and that operators are not expected to change executor security environment variables (network egress excepted).
- **FR-019a**: The operator guidance for FR-017–FR-019 MUST live in the repository **README** (or a top-level doc it prominently links) so it is discoverable on first read, per the US3 Independent Test — a new operator finds the image reference and minimum scratch size without consulting the maintainers.

### Key Entities *(include if feature involves data)*

- **Build image**: The purpose-built container image (defined by the in-repo Dockerfile, published to GHCR). Key attributes: pinned tool versions (stable Rust toolchain, C linker, curl, oras), immutable digest reference, usable by the unprivileged default user.
- **Build script**: The run-time build logic (`scripts/build-rust.sh` and related glue). Key attributes: runs rootless under read-only rootfs and read-only workspace, writes only to the scratch mount, no run-time installs, emits `BINARY_SHA256` / `BINARY_OCI_REF`, pushes via oras.
- **Writable scratch mount**: The executor-provided tmpfs/scratch directory — the only writable location. Key attributes: hosts the toolchain's writable home(s), the cargo target directory, downloads, and credentials; must meet the documented minimum size.
- **Attested OCI artifact**: The final `attested-hello` output published to GHCR with its verified digest, consumed by downstream attestation steps.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the executor on its default hardened configuration (only network egress allowed) and zero changes to its user, rootfs, workspace mode, capability, or no-new-privileges settings, dispatching the workflow produces an attested OCI artifact in GHCR with a verified digest.
- **SC-002**: The build performs zero run-time software installations (no package-manager invocation, no toolchain download) during a successful run.
- **SC-003**: 100% of the build's write operations occur within the writable scratch mount; none touch the root filesystem or the workspace.
- **SC-004**: Every tool the build requires (Rust toolchain, C linker, curl, oras) is present and runnable in the image before the build starts, verifiable without network access for anything except the final GHCR push.
- **SC-005**: The build environment is reproducible: the image is referenceable by an immutable digest and its tool versions are pinned, so two builds from the same image digest use identical tooling.
- **SC-006**: An operator, using only the repository documentation, can identify the correct build image reference and the minimum scratch size and run a successful build without consulting the maintainers.
- **SC-007**: The `BINARY_SHA256` and `BINARY_OCI_REF` markers and the oras-push transfer mechanism continue to function unchanged from the consumer (workflow) perspective.

> **Traceability note**: Every functional requirement maps to at least one measurable Success Criterion. Representative mapping for the requirements added during checklist resolution: FR-004/FR-004a/FR-005a → SC-005 (reproducibility) and SC-001 (attested artifact); FR-006/FR-007 (user/caps) → SC-001, SC-003; FR-010/FR-010a (scratch-only writes) → SC-003; FR-016/FR-016a/FR-016b (clear failure, no partial artifact, egress failure) → SC-001 (a run either yields a verified artifact or fails clearly) and SC-002/SC-004 (no fallback installs); FR-017–FR-019a (docs) → SC-006.

## Assumptions

- **Network egress is available** to the execution container and is the single permitted exception to the hardened defaults; removing the oras/GHCR network dependency is explicitly out of scope.
- **Security settings are executor-global, not per-request**: the container user, rootfs mode, workspace mode, capabilities, and no-new-privileges are fixed on the executor and cannot be set per build request, so all compatibility must come from the image and the build script.
- **Scratch is a tmpfs sized by the operator**: a Rust release build of this project fits within a modest scratch allocation. To avoid under-provisioning risk for operators, the spec states a **conservative minimum floor of 4 GiB** of writable scratch — covering the toolchain's writable home(s), downloaded artifacts, and the release `target/` directory with comfortable headroom. Implementation may confirm a lower typical usage, but the documented operator requirement is the 4 GiB floor.
- **The toolchain's writable state is relocatable**: cargo/rustup can be directed (via their home/target settings) to write under the scratch mount while still using the pre-installed, read-only toolchain binaries from the image.
- **Target platform is linux/amd64**, consistent with the current build and tooling assumptions.
- **GHCR and the existing GitHub token/credential flow remain the publish path**; this feature changes how the build environment is provisioned, not where artifacts are published or how they are attested downstream.
- **The image is built/published by a CI workflow in this repository** to this repo's GHCR namespace (see FR-005a) and referenced by the executor; standing up that image-publishing automation is part of this feature's deliverables, but the executor consumes the pre-published image (by digest) rather than building it per run.

## Dependencies

- The Remote Executor (separate repo: `github-runner-ec2-attestation`) running its hardened default container-security configuration, with outbound network egress enabled.
- GHCR availability for both pulling the build image and pushing the attested artifact.
- The existing workflow glue that parses `BINARY_SHA256` / `BINARY_OCI_REF` and performs downstream digest verification and attestation, which this feature must keep compatible.
