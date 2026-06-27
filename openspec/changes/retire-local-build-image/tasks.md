## 1. Pre-flight

- [x] 1.1 Confirm `github-runner-ec2-attestation/flavors/rust-build/Dockerfile` still matches this repo's `Dockerfile` on every functional instruction (FROM digest, apt pins, rustup-init sha, Rust `1.96.0`, oras `1.3.2` sha, `/opt/rust`, `USER 65534:65534`) — i.e. the only deltas are comments — so deletion loses nothing.
- [x] 1.2 Confirm `flavors/rust-build/env` upstream lists this repo in `ALLOWED_REPOSITORIES`, so the flavor authorizes builds from here.
- [x] 1.3 Confirm `.github/workflows/attested-rust-build.yml` does not reference `build-image.yml` or an in-repo `build-image` digest (workload/dispatch side is independent).

## 2. Spec source-of-truth flip

- [x] 2.1 Update `openspec/specs/hardened-build-environment/spec.md` Purpose prose to drop "the purpose-built container build image" ownership and reference the upstream `rust-build` flavor as the image provider (the requirement deltas are applied from this change at archive).
- [x] 2.2 Verify the delta spec validates: `openspec validate retire-local-build-image --strict`.

## 3. Documentation

- [x] 3.1 Rewrite README "Configuring the Hardened Build Image (Operator Setup)": remove the `ghcr.io/<owner>/…/build-image@sha256:<digest>` pin/re-pin guidance and the "Build Hardened Build Image workflow job summary" instructions.
- [x] 3.2 Replace it with guidance pointing operators at the upstream `rust-build` flavor (`github-runner-ec2-attestation/flavors/rust-build/`) and its per-flavor, PCR4-bound attestable AMI as the source of the execution image.
- [x] 3.3 Keep the "Minimum writable scratch-mount size (≥ 4 GiB)" and "No executor security changes required" subsections (still accurate).

## 4. Toolchain contract

- [x] 4.1 Document the toolchain contract where `scripts/build-rust.sh` declares its `RUSTUP_HOME` / `RUST_TOOLCHAIN_BIN` defaults (`build-rust.sh:69,74`): note these values are the contract provided by the upstream `rust-build` flavor (`oras`, `cargo`/`rustc`/`cc`/`curl` on PATH for `65534:65534`; `RUSTUP_HOME=/opt/rust`; toolchain bin `/opt/rust/toolchains/1.96.0-x86_64-unknown-linux-gnu/bin`).
- [x] 4.2 Do NOT change any default values — they must stay byte-identical to the upstream flavor.

## 5. Remove obsolete artifacts

- [x] 5.1 Delete `Dockerfile`.
- [x] 5.2 Delete `.github/workflows/build-image.yml`.
- [x] 5.3 Remove the US2 `test_dockerfile_*` cases (and the `dockerfile_text` fixture / `read_dockerfile` / `DOCKERFILE_PATH` loaders if now unused) from `tests/test_build_image_hardening.py`; keep all US1 `test_build_script_*` cases.

## 6. Verify

- [x] 6.1 Run `pytest` — the retained US1 build-script cases pass; no test references the deleted `Dockerfile`.
- [x] 6.2 Grep the repo for stale references to `build-image`, `Build Hardened Build Image`, and `build-image.yml`; confirm none remain in README, workflows, or tests.
- [x] 6.3 Re-run `openspec validate retire-local-build-image --strict`.
