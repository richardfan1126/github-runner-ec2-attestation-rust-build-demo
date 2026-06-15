# Contract: Build Image (`Dockerfile` → GHCR)

The guarantees the purpose-built image makes to the build script and the operator.

## Identity & platform

- Platform: `linux/amd64`.
- Default user: `65534:65534` (set via `USER 65534:65534`) — matches the executor's hardened default (FR-006).
- No reliance on a writable `$HOME`; tool state dirs are set explicitly by the build script.

## Pinning guarantees (reproducibility — FR-004, FR-004a, SC-005)

| Component | Pinned form |
|---|---|
| Base image | `FROM <image>@sha256:<digest>` (content digest, never a floating tag) |
| Rust toolchain | rustup, **exact** stable channel `X.Y.Z` (e.g. `1.96.0`, never `stable`), `--profile minimal` |
| C compiler/linker | distro package(s) `gcc=<ver>` / `libc6-dev=<ver>` (exact) |
| curl | distro package `curl=<ver>` (exact) |
| oras CLI | release tarball `1.3.2`, **SHA-256-verified** before extract |
| rustup installer | downloaded then **SHA-256-verified** before execution |

Any out-of-band download in the image build **must** be SHA-256-verified before use.

## Tool availability (FR-002, FR-003, FR-006)

The following are present and runnable by `65534` with **no** run-time install and **no** network (except the build script's own final GHCR push):

- `cargo` and `rustc` (the pinned stable toolchain)
- `cc` (C compiler) + working linker for the final link step
- `curl`
- `oras`

All reachable on `ENV PATH`. Invoking the real `cargo`/`rustc` binaries directly (not rustup proxies) so `RUSTUP_HOME` need not be writable at run time (research R1).

## Publication (FR-005, FR-005a)

- Built from the checked-in `Dockerfile` by `.github/workflows/build-image.yml`.
- Pushed to `ghcr.io/<owner>/<repo>/build-image`.
- Referenceable by an **immutable digest** (`…@sha256:<digest>`), which the workflow surfaces (see `image-publish-ci.md`).
- Image-self provenance/attestation is **out of scope** — only the build's output artifact is attested.

## Non-goals

- Not multi-arch (linux/amd64 only).
- No `ENTRYPOINT`/`CMD` that runs the build — the executor invokes the script; the image only provides the environment.
