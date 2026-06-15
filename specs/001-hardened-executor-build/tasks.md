---
description: "Task list for Hardened-Executor-Compatible Attested Build"
---

# Tasks: Hardened-Executor-Compatible Attested Build

**Input**: Design documents from `/specs/001-hardened-executor-build/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Static/property tests over the `Dockerfile` and `scripts/build-rust.sh` **are in scope** for this feature — plan.md names `tests/test_build_image_hardening.py` as a deliverable and the contracts/quickstart describe the invariants to assert. They are text-inspection tests (matching `tests/test_security_hardening.py` style), not container/integration tests; container validation is the manual `quickstart.md`.

**Organization**: Tasks are grouped by user story (priority order from spec.md) to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Paths are repository-relative from the repo root.

## Path Conventions

Single repo — container image + shell build script + CI workflow + docs. Primary artifacts: `Dockerfile`, `scripts/build-rust.sh`, `.github/workflows/build-image.yml`, `README.md`, `tests/test_build_image_hardening.py`. No application source layout change.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Capture the concrete, verifiable literals the image build needs (research fixes the *method*; the Dockerfile needs *real values*).

- [X] T001 Capture and record the exact pinned literals required by the `Dockerfile`: the `debian:bookworm-slim` base image **content digest** (`docker buildx imagetools inspect debian:bookworm-slim`, per research R2); the exact stable rustup channel `X.Y.Z` (current stable, never `stable`, per R3); the exact apt package versions for `gcc` / `libc6-dev` / `curl` resolved against the pinned base's apt index (R3); the `oras` `1.3.2` `linux_amd64` tarball **SHA-256** from the release `*_checksums.txt`; and the rustup installer **SHA-256**. Record these values in `specs/001-hardened-executor-build/research.md` (or a short note alongside it) so T009 can hard-code them verbatim.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared test scaffolding used by both the US1 (script) and US2 (Dockerfile) static-test tasks.

**⚠️ CRITICAL**: The story-specific test tasks (T003, T008) depend on this module existing.

- [X] T002 Create the shared static-test module `tests/test_build_image_hardening.py` with pytest scaffolding and helpers that load the raw text of `Dockerfile` and `scripts/build-rust.sh` from the repo root (mirror the path/text-inspection style of `tests/test_security_hardening.py`). No assertions yet — just the loader fixtures/helpers the per-story tests will use.

**Checkpoint**: Pinned literals captured + shared test module exists — user story work can begin.

---

## Phase 3: User Story 1 - Build succeeds under the executor's hardened defaults (Priority: P1) 🎯 MVP

**Goal**: Rewrite `scripts/build-rust.sh` so it runs rootless (`65534:65534`) under read-only rootfs + read-only `/workspace`, writes only beneath the single tmpfs scratch mount, performs zero run-time installs, and still emits the `BINARY_SHA256` / `BINARY_OCI_REF` markers and pushes via oras unchanged.

**Independent Test**: Static tests (T003) assert the script invariants directly. Full end-to-end validation (quickstart Scenario B — attested artifact lands in GHCR under hardened `docker run` flags) additionally requires the US2 image; see Dependencies.

### Tests for User Story 1

- [ ] T003 [US1] In `tests/test_build_image_hardening.py`, add static assertions over `scripts/build-rust.sh` text: contains **no** `apt-get`/`dnf`/`yum`/`apk` install and **no** `sh.rustup.rs`/rustup-download; sets `CARGO_HOME` and `CARGO_TARGET_DIR` relative to the scratch root (`BUILD_SCRATCH_DIR`/`/tmp`); sets `RUSTUP_HOME` to the **read-only image toolchain path** (NOT under scratch — per spec FR-010 clarification 2026-06-15); preflights tools via `command -v` with a tool-naming `die`; performs a scratch write-probe naming the path; emits both `BINARY_SHA256:` and `BINARY_OCI_REF:` markers; and the marker emission appears **after** the oras push step in the file (markers only on full success). (Traces FR-003, FR-010, FR-011, FR-016.)

### Implementation for User Story 1

- [ ] T004 [US1] In `scripts/build-rust.sh`, resolve the scratch root `SCRATCH_DIR="${BUILD_SCRATCH_DIR:-/tmp}"` and define all write targets as subdirs beneath it (`CARGO_HOME=$SCRATCH_DIR/.cargo`, `CARGO_TARGET_DIR=$SCRATCH_DIR/target`, source copy `$SCRATCH_DIR/rust-project`, `oras-auth.json`, oras scratch); set `RUSTUP_HOME` to the read-only image toolchain path (set, never written — research R1) and prepend the toolchain `bin` (real `cargo`/`rustc`, not rustup proxies) + `/usr/local/bin` to `PATH`. **Delete** the legacy "Step 0" system-dependency install block, the rustup install branch, the oras tarball download, and the now-dead `download()` helper (FR-003, FR-010, FR-010a, FR-011; research R1/R5/R7).
- [ ] T005 [US1] In `scripts/build-rust.sh`, implement the preflight: keep the required-env-var checks (`GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `COMMIT_SHA` via `: "${VAR:?…}"`); add `command -v cargo rustc cc curl oras`, failing with `die "required tool not found: <name>"` naming the specific missing tool (note: `cc` must resolve on PATH — guaranteed by T009's `cc`→`gcc` alternative/symlink); add a scratch write-probe (`: > "$SCRATCH_DIR/.write-probe"` else `die` naming the scratch path) — this is the **only** proactive script-level filesystem check. Do **not** attempt to pre-validate or wrap other write targets for "outside scratch": per spec FR-016 clarification 2026-06-15, out-of-scratch writes are **OS-enforced** (read-only rootfs/workspace returns `EROFS`); the script surfaces the failing command's error and never retries with privilege. All non-zero exit (FR-007, FR-016; research R6).
- [ ] T006 [US1] In `scripts/build-rust.sh`, stage `/workspace/rust-project` → `$SCRATCH_DIR/rust-project` (copy; never write under read-only `/workspace`), run `cargo build --release` with the scratch `CARGO_HOME`/`CARGO_TARGET_DIR`, then assert the binary exists at `$SCRATCH_DIR/target/release/attested-hello` and compute its SHA-256 over the complete file. On non-zero compile/link exit, `die` naming the failing build step / that the writable scratch mount was exhausted (FR-009, FR-010, FR-015, FR-016, FR-016a; research R6).
- [ ] T007 [US1] In `scripts/build-rust.sh`, perform `oras login` + `oras push` of the binary to the temporary GHCR package; on failure `die` naming the push/egress failure (non-zero exit, no markers — FR-016b). Emit `BINARY_SHA256:<digest>` and `BINARY_OCI_REF:<ref>` **only after** the push succeeds, in their current format (FR-013, FR-014, FR-016a, SC-007). Update the script's header comment block to describe the hardened, pre-installed-toolchain, scratch-only behavior (drop the "Installs the Rust toolchain" wording).

**Checkpoint**: T003 passes; the script is rootless, scratch-only, install-free, and marker-compatible (statically verified).

---

## Phase 4: User Story 2 - Reproducible, pinned build image as a supply-chain anchor (Priority: P2)

**Goal**: A checked-in `Dockerfile` that ships cargo/rustc, cc/linker, curl, and oras pre-installed at pinned versions on a digest-pinned base, runnable as `65534:65534`; plus a CI workflow that builds it, pushes to GHCR, and surfaces the immutable digest.

**Independent Test**: Build the image from the Dockerfile and confirm each tool is present at its pinned version and runnable by `65534` offline (quickstart Scenario A); confirm the published image is referenceable by an immutable digest. Static tests (T008) assert the Dockerfile pinning/hardening invariants.

### Tests for User Story 2

- [ ] T008 [US2] In `tests/test_build_image_hardening.py`, add static assertions over `Dockerfile` text: `FROM` line carries `@sha256:`; rustup is installed at a concrete `X.Y.Z` channel (not `stable`) with `--profile minimal`; apt installs use `pkg=<ver>` pinning for the C linker package(s) and `curl`; the oras tarball and rustup installer downloads are each followed by a `sha256sum -c`/checksum comparison before use; a `USER 65534:65534` line exists; no `CMD`/`RUN` performs an unguarded run-time-style install; tool dirs are on `ENV PATH`. (Traces FR-004, FR-004a, FR-006, FR-002.)

### Implementation for User Story 2

- [ ] T009 [US2] Create `Dockerfile` (repo root) using the literals captured in T001: `FROM debian:bookworm-slim@sha256:<digest>`; `apt-get install -y --no-install-recommends gcc=<ver> libc6-dev=<ver> curl=<ver>` (pinned). Install the **`gcc` meta-package** (not a bare `gcc-12` versioned package): its postinst registers the `/usr/bin/cc` update-alternatives entry, so `cc` resolves on PATH for uid `65534` — required because both T005's preflight and cargo's link step invoke `cc`. (If a versioned compiler package is used instead, add the alternative explicitly: `update-alternatives --install /usr/bin/cc cc /usr/bin/gcc 100`.) T008 should assert `cc` is present; download the rustup installer + SHA-256-verify it, then `rustup` install the exact stable channel `X.Y.Z` with `--profile minimal` into a world-readable path (e.g. `/opt/rust`); download `oras_1.3.2_linux_amd64.tar.gz`, SHA-256-verify against the recorded checksum, extract `oras` to `/usr/local/bin`; set `ENV PATH` to include the toolchain `bin` + `/usr/local/bin` and `ENV RUSTUP_HOME` to the read-only toolchain home; end with `USER 65534:65534`; **no** `CMD`/`ENTRYPOINT` that runs the build (FR-001–FR-006, FR-004a; research R2/R3/R4).
- [ ] T010 [P] [US2] Create `.dockerignore` (repo root) excluding `.git`, `.venv`, `.pytest_cache`, `.hypothesis`, `dist/`, `specs/`, and other non-image context so the build context stays minimal and reproducible.
- [ ] T011 [US2] Create `.github/workflows/build-image.yml`: trigger on `workflow_dispatch` (and `push` affecting `Dockerfile` / `scripts/build-rust.sh`); `permissions: { contents: read, packages: write }`; steps = checkout → `docker/login-action` to `ghcr.io` with `GITHUB_TOKEN` → `docker/build-push-action` building `Dockerfile` for `linux/amd64`, pushing to `ghcr.io/${{ github.repository }}/build-image:<tag>`, capturing `outputs.digest` → write the immutable `ghcr.io/${{ github.repository }}/build-image@<digest>` reference to `$GITHUB_STEP_SUMMARY`. No image-self provenance/attestation (FR-005, FR-005a; research R8).

**Checkpoint**: T008 passes; the image builds, ships every pinned tool runnable as `65534`, and the workflow surfaces an immutable digest.

---

## Phase 5: User Story 3 - Operator knows exactly how to point the executor at the image (Priority: P3)

**Goal**: README guidance so a fresh operator can configure the executor's build image and scratch size on the first try.

**Independent Test**: A new operator, reading only `README.md`, can identify the exact `…@sha256:<digest>` image reference and the ≥4 GiB scratch floor, then run a build with no executor security change (egress excepted).

### Implementation for User Story 3

- [ ] T012 [US3] Update `README.md` with an operator-guidance section: the exact build-image reference expressed as an **immutable digest** (`ghcr.io/<owner>/<repo>/build-image@sha256:<digest>`, never a floating tag — FR-017); the **minimum writable scratch-mount size** as a conservative ≥4 GiB floor, stating its **basis** (toolchain writable home/caches + downloaded artifacts + release `target/` + headroom) and noting it MAY be validated/lowered by measuring peak scratch (FR-018); and a clear statement that compatibility comes from the image + build script alone — operators change **no** executor security setting (user, rootfs/workspace mode, caps, no-new-privileges), with network egress the sole pre-existing exception (FR-019, FR-019a; research R9).

**Checkpoint**: All three user stories are independently satisfiable from their artifacts.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation and compatibility confirmation across stories.

- [ ] T013 [P] Run the static suite: `pytest tests/test_build_image_hardening.py -q` — confirm all Dockerfile + build-rust.sh invariants pass (FR-003, FR-004, FR-004a, FR-006, FR-010, FR-011, FR-016).
- [ ] T014 Execute `quickstart.md` Scenarios A–D manually (requires Docker): A (tools present/pinned/runnable as `65534`, offline), B (build under full hardened constraints → attested artifact + correct markers), C (attributable failures, no markers), D (measure peak scratch). Record the Scenario D `PEAK_SCRATCH_MB` to inform the FR-018 floor.
- [ ] T015 Confirm consumer compatibility is unbroken: `.github/workflows/attested-rust-build.yml` is left **unchanged**, and the existing marker/oras tests still pass (`pytest tests/test_property_markers.py tests/test_output_polling.py -q`) so the `BINARY_SHA256` / `BINARY_OCI_REF` grep contract and oras push still match (FR-013, FR-014, SC-007).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately. T001 produces literals consumed by T009.
- **Foundational (Phase 2)**: T002 creates the shared test module that T003 and T008 extend. Blocks those test tasks.
- **User Stories (Phase 3–5)**: Depend on Phase 2 for their test tasks. Implementation tasks within a story can otherwise proceed once their inputs exist.
- **Polish (Phase 6)**: Depends on the stories whose artifacts it validates.

### Cross-Story Dependency (important)

- **US1 static tests (T003) are independent** of US2 — they inspect script text only.
- **US1 *end-to-end* validation** (quickstart Scenario B in T014) **requires the US2 image** (the pre-installed toolchain is what makes the hardened-defaults run possible — spec US2 rationale). So the runnable MVP demo needs US1 **and** US2 together; the script (US1) can still be authored and unit-validated first.
- **T009 (Dockerfile) depends on T001** (pinned literals).
- **US3 (T012) README digest** is most accurate after **T011** has produced a real published digest, but can be drafted earlier with a placeholder.

### Within Each User Story

- US1: T004 → T005 → T006 → T007 are sequential (all edit `scripts/build-rust.sh`). T003 (test) is independent of the script edits and can be written first.
- US2: T009 and T011 are largely independent of each other; T010 (`.dockerignore`) is fully independent [P]. T008 (test) is independent of the US2 implementation tasks but shares the test file with T003 (not [P] relative to each other).

### Parallel Opportunities

- T003 (US1 script tests) and T008 (US2 Dockerfile tests) are both independent of the *implementation* tasks once T002 exists, but they edit the **same file** (`tests/test_build_image_hardening.py`) and so must be written **sequentially**, not in parallel. (To parallelize them, split into two test files — see Notes.)
- T010 [P] (`.dockerignore`) parallel with any other US2 task.
- Across stories: once Phase 2 is done, US1 (script) and US2 (image) can be developed in parallel by different people; US3 docs in parallel with a placeholder digest.
- T013 [P] parallel with other polish reads.

---

## Parallel Example

```bash
# T003 and T008 both edit tests/test_build_image_hardening.py — write them SEQUENTIALLY (not in parallel).

# Genuinely parallelizable work once Phase 2 is done — different files:
Task: "T010 [US2] Create .dockerignore"
Task: "T009 [US2] Create Dockerfile"          # different file from .dockerignore / workflow
Task: "T004 [US1] Rewrite top of scripts/build-rust.sh"   # different story, different file
```

---

## Implementation Strategy

### MVP First

The minimal demoable outcome (P1) is a hardened-defaults build that produces an attested artifact. Because the run-time build needs the pre-installed image, the practical MVP is **US1 + US2 together**:

1. Phase 1 (T001 literals) + Phase 2 (T002 test module).
2. US2 image (T008–T011) so the toolchain exists pre-installed.
3. US1 script (T003–T007) so it runs rootless, scratch-only, install-free.
4. **STOP & VALIDATE**: quickstart Scenarios A + B (T014) — attested artifact lands in GHCR under full hardened `docker run` flags with no executor change.

### Incremental Delivery

1. US1 script + its static tests → statically verified hardening invariants.
2. US2 image + workflow → reproducible pre-installed toolchain, published digest.
3. US1 + US2 → runnable hardened build (MVP demo).
4. US3 README → a fresh operator can configure and run unaided.
5. Polish → confirm no consumer (existing workflow) regressions.

---

## Notes

- [P] = different files / no incomplete dependency. The four US1 script tasks share `scripts/build-rust.sh` and are therefore sequential, not [P]. Likewise T003 and T008 share `tests/test_build_image_hardening.py` so they are sequential, not [P] — both are still independent of the *implementation* tasks and can precede them. If true parallelism of the two test tasks is desired, split the module into `tests/test_buildscript_hardening.py` (T003) and `tests/test_dockerfile_hardening.py` (T008); then both become `[P]` and T002's shared loader helpers move into a small `conftest.py`/helper imported by both.
- The existing `.github/workflows/attested-rust-build.yml` is **deliberately not modified** — its compatibility is an acceptance criterion (SC-007).
- Tests here are static text inspection (no Docker); container behavior is validated via the manual `quickstart.md`.
- Commit after each task or logical group.
