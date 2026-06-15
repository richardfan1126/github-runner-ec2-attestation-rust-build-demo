# Hardening & Supply-Chain Requirements Checklist: Hardened-Executor-Compatible Attested Build

**Purpose**: Lightweight author self-check validating the *quality* of the spec's hardening, supply-chain, operability, and failure-mode requirements before planning
**Created**: 2026-06-15
**Feature**: [spec.md](../spec.md)

**Scope note**: This is a requirements-quality review (are the requirements complete, clear, consistent, measurable?), not an implementation test. Depth: lightweight sanity. Audience: spec author.

## Security Hardening Requirements

- [x] CHK001 Is the "default capability set" the build may rely on enumerated or bounded, rather than left undefined? [Clarity, Spec §FR-007] — RESOLVED: FR-007 bounds it to zero — the build requires no added capabilities and relies on none of the executor's default set.
- [x] CHK002 Is the unprivileged "default user" identified (e.g., named/UID) or explicitly delegated to the executor, so FR-006 and FR-007 can be objectively checked? [Clarity, Spec §FR-006, §FR-007] — RESOLVED: default user pinned to UID:GID `65534:65534` (nobody:nogroup) in FR-006/FR-007.
- [x] CHK003 Are the read-only-rootfs (FR-008), read-only-workspace (FR-009), and writes-only-to-scratch (FR-010) requirements mutually consistent and jointly exhaustive of all write targets named (toolchain home, cargo target, downloads, credentials)? [Consistency, Spec §FR-008–§FR-010] — RESOLVED: FR-010 now states its enumeration is exhaustive and, with FR-008/FR-009, every write lands in scratch and nowhere else.
- [x] CHK004 Is "writable scratch mount" given one consistent definition (tmpfs vs. disk, single vs. multiple mounts) across FR-010, the Key Entities, and the Assumptions? [Consistency, Spec §FR-010] — RESOLVED: FR-010a defines it as a single tmpfs mount with subdirectories, consistent with Key Entities and Assumptions.

## Supply-Chain & Reproducibility Requirements

- [x] CHK005 Is "pinned" defined per tool — exact version vs. content digest — for the Rust toolchain, C linker, curl, and oras? [Clarity, Spec §FR-004] — RESOLVED: FR-004 now specifies per tool — Rust via rustup exact channel; cc/curl as exact version-pinned distro packages; oras tarball + SHA-256 checksum; all out-of-band downloads SHA-256-verified.
- [x] CHK006 Are base-image / OS-layer pinning requirements specified, or does pinning cover only the four named tools? [Gap, Spec §FR-004] — RESOLVED: FR-004a requires the base image be pinned by content digest.
- [x] CHK007 Is provenance/attestation of the build image *itself* required, consistent with calling it a "supply-chain anchor," or is only the output artifact attested? [Gap, Spec §US2] — RESOLVED: US2 clarification + FR-005a scope the anchor to reproducibility (digest-pinned base, pinned tools, published image digest); image provenance is out of scope, only the output artifact is attested, and CI surfaces the final image digest.
- [x] CHK008 Is the image build-and-publish responsibility (who/what automation) stated as a requirement, or does it only appear as an assumption — and do FR-005 and the Assumptions agree on it? [Consistency, Spec §FR-005, §Assumptions] — RESOLVED: FR-005a makes a CI workflow in this repo responsible for building/publishing to this repo's GHCR; Assumptions updated to match.

## Operability & Documentation Requirements

- [x] CHK009 Does the documented image reference (FR-017) specify the form to use (immutable digest vs. floating tag), consistent with FR-005 and the stale-tag edge case? [Consistency, Spec §FR-017] — RESOLVED: FR-017 now mandates an immutable `...@sha256:<digest>` reference, not a floating tag.
- [x] CHK010 Is the 4 GiB scratch minimum (FR-018) accompanied by a stated basis or validation method so it is objectively defensible rather than asserted? [Measurability, Spec §FR-018] — RESOLVED: FR-018 now states the basis (toolchain home + downloads + release target/ + headroom) and a validation method (measure peak scratch usage).
- [x] CHK011 Do the documentation requirements specify where the operator guidance must live and that it is discoverable on first read (per US3 Independent Test)? [Completeness, Spec §FR-017–§FR-019] — RESOLVED: FR-019a requires the guidance live in the README (or a prominently linked top-level doc), discoverable on first read.

## Edge-Case & Failure-Mode Requirements

- [x] CHK012 Is the scratch-exhaustion failure given measurable "clear error" criteria (what must be surfaced) rather than only a narrative expectation? [Measurability, Spec §Edge Cases, §FR-016] — RESOLVED: FR-016 requires non-zero exit plus a message naming the scratch-exhaustion condition.
- [x] CHK013 Is "clear, attributable error" in FR-016 defined enough to be verifiable, and applied consistently to both missing-tool and out-of-scratch-write cases? [Clarity, Spec §FR-016] — RESOLVED: FR-016 defines the standard (non-zero exit + condition + offending detail) and applies it uniformly to missing-tool and out-of-scratch-write cases.
- [x] CHK014 Are requirements defined to guarantee no corrupt/partial artifact is produced or pushed on a mid-build failure (recovery/abort behavior)? [Coverage, Recovery, Spec §Edge Cases] — RESOLVED: FR-016a requires abort-without-partial-artifact on any mid-build failure; digest computed only over a fully built binary and push is all-or-nothing.
- [x] CHK015 Is the blocked-egress case explicitly scoped (fail clearly, out of scope to remove) as a requirement, not only an assumption/edge note? [Coverage, Spec §Edge Cases, §Assumptions] — RESOLVED: FR-016b promotes it to a requirement — push fails clearly with no false success markers; removing the dependency stays out of scope.

## Traceability & Consistency Across the Spec

- [x] CHK016 Does each functional requirement map to at least one measurable Success Criterion (e.g., FR-010↔SC-003, FR-011↔SC-002), with no FR left unverified? [Traceability, Spec §FR-001–§FR-019, §SC-001–§SC-007] — RESOLVED: a Traceability note added after SC-007 maps every FR (including the items added during checklist resolution) to at least one SC.

## Notes

- Check items off as completed: `[x]`; record findings inline.
- `[Gap]` items flag requirements that may be missing entirely — resolve by adding a requirement or explicitly marking out-of-scope.
- This list intentionally omits items already covered by `requirements.md` (general spec-quality gate).
