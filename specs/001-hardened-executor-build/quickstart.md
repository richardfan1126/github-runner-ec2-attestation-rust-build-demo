# Quickstart: Validating the Hardened-Executor-Compatible Build

**Feature**: `001-hardened-executor-build` | **Date**: 2026-06-15

Runnable validation that the build image + script satisfy the hardened-defaults requirements **without** changing any executor security setting. This locally reproduces the executor's hardened constraints with `docker run` flags.

References: [contracts/build-image.md](./contracts/build-image.md), [contracts/build-script.md](./contracts/build-script.md), [data-model.md](./data-model.md).

## Prerequisites

- Docker with BuildKit (`linux/amd64`).
- A `GITHUB_TOKEN` (PAT or Actions token) with `packages:write` for the push scenario (Scenario B). Scenarios A, C, D can run without a token.

> **Docker tmpfs note**: Docker's `--tmpfs` adds `noexec` by default (real Linux executor mounts are exec-capable by default). All scenarios below use `exec` in the tmpfs options so cargo's build scripts — which are compiled and executed — can run locally.

## Scenario A — Image ships every tool, pinned, runnable as `65534` (US2)

Build the image and confirm the toolchain is present at pinned versions for the unprivileged default user — **no installs, no network**.

```bash
# Build from the checked-in Dockerfile
docker build -t hardened-build:local .

# Tools present and runnable as the default user (65534), offline
docker run --rm --network none hardened-build:local sh -c '
  set -e
  id
  command -v cargo rustc cc curl oras
  cargo --version
  rustc --version
  oras version
'
```

**Expected**: `uid=65534 gid=65534`; every tool resolves; versions match the pinned values in the `Dockerfile`. Exit 0 with no network. (Validates FR-002, FR-003, FR-006, SC-004.)

## Scenario B — Build under full hardened constraints, writes only to scratch (US1, P1)

Run the build script as `65534`, **read-only rootfs**, **read-only workspace**, **no added caps**, **no-new-privileges**, single **tmpfs scratch**. (Network left on only because the script's final step pushes to GHCR; use Scenario B-offline below to prove the compile half needs no network.)

```bash
docker run --rm \
  --user 65534:65534 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,exec,size=4g \
  -v "$PWD":/workspace:ro \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -e GITHUB_REPOSITORY="<owner>/<repo>" \
  -e COMMIT_SHA="$(git rev-parse HEAD)" \
  hardened-build:local \
  bash /workspace/scripts/build-rust.sh
```

**Expected**: build compiles, computes the digest, pushes, and prints on stdout:
```
BINARY_SHA256:<64-hex>
BINARY_OCI_REF:ghcr.io/<owner>/<repo>/tmp-build:<tag>
```
No write touches `/` or `/workspace` (they are read-only — any stray write would fail fast). (Validates FR-007–FR-014, SC-001, SC-003, SC-007.)

### B-offline — prove the compile half needs no network

Re-run Scenario B with `--network none` and a script stop-before-push (or expect the push step to be the *only* failure). The compile + digest must succeed offline; only `oras login/push` fails, and it fails **clearly** naming the egress/push failure with **no** success markers (FR-016b).

## Scenario C — Failure modes are clear and attributable (FR-016)

```bash
# Missing tool: shadow PATH so tools are not found → names the missing tool
docker run --rm --user 65534:65534 --read-only --tmpfs /tmp:rw,exec,size=4g \
  -v "$PWD":/workspace:ro -e PATH=/nonexistent \
  -e GITHUB_TOKEN=x -e GITHUB_REPOSITORY=o/r -e COMMIT_SHA=deadbeef \
  hardened-build:local /bin/bash /workspace/scripts/build-rust.sh; echo "exit=$?"

# Scratch too small: shrink tmpfs below the build's need → names the failing step
docker run --rm --user 65534:65534 --read-only --tmpfs /tmp:rw,exec,size=8m \
  -v "$PWD":/workspace:ro \
  -e GITHUB_TOKEN=x -e GITHUB_REPOSITORY=o/r -e COMMIT_SHA=deadbeef \
  hardened-build:local /bin/bash /workspace/scripts/build-rust.sh; echo "exit=$?"
```

**Expected**: non-zero exit in each case; stderr names the specific condition (missing tool name / failing build step) and offending detail; **no** `BINARY_SHA256`/`BINARY_OCI_REF` emitted. (Validates FR-016, FR-016a.)

## Scenario D — Measure actual peak scratch (informs FR-018)

```bash
# Watch tmpfs high-water mark during a release build to validate/lower the 4 GiB floor
docker run --rm --user 65534:65534 --read-only --tmpfs /tmp:rw,exec,size=4g \
  -v "$PWD":/workspace:ro \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" -e GITHUB_REPOSITORY="<owner>/<repo>" \
  -e COMMIT_SHA="$(git rev-parse HEAD)" \
  hardened-build:local /bin/bash -c '
    /bin/bash /workspace/scripts/build-rust.sh & pid=$!
    peak=0; while kill -0 $pid 2>/dev/null; do
      u=$(du -sm /tmp 2>/dev/null | cut -f1); [ "${u:-0}" -gt "$peak" ] && peak=$u; sleep 1; done
    wait $pid || true
    echo "PEAK_SCRATCH_MB=$peak"'
```

**Expected**: `PEAK_SCRATCH_MB` well under 4096 (zero crate deps). Documented floor stays 4 GiB until measured down. (Validates FR-018 basis/validation method.)

**Measured (2026-06-15, attested-hello with zero crate deps)**: `PEAK_SCRATCH_MB` is negligible (<1 MB for source staging + <1 s compile); the 4 GiB floor is appropriate for production use with real dependency trees.

## Static invariant checks (no Docker needed)

```bash
uv run --extra dev pytest tests/test_build_image_hardening.py -q
```

**Expected**: passes — asserts Dockerfile digest-pins the base, pins each tool, SHA-256-verifies downloads, sets `USER 65534`; and asserts `build-rust.sh` has no run-time installs, sets cargo/rustup state under scratch, preflights tools with named errors, and emits markers only after push. (Validates FR-003, FR-004, FR-004a, FR-006, FR-010, FR-011, FR-016.)

## Operator path (US3)

From the **README** alone an operator should find: the exact `…@sha256:<digest>` build-image reference to point the executor at, and the ≥4 GiB scratch floor — then run a build with **no** executor security change (egress excepted). (Validates FR-017–FR-019a, SC-006.)

## Done when

- [X] Scenario A: tools present + pinned + runnable as `65534`, offline.
- [ ] Scenario B: attested artifact pushed under full hardened constraints; markers correct.
- [X] Scenario C: each failure exits non-zero with an attributable message and no markers.
- [X] Scenario D: peak scratch measured and recorded (negligible for zero-dep project; 4 GiB floor appropriate for production).
- [X] Static tests pass.
- [X] README lets a fresh operator configure image ref + scratch with no maintainer help.
