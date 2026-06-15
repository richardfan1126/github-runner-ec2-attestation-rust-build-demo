# Contract: Build Script (`scripts/build-rust.sh`)

The interface the Remote Executor and the consuming workflow rely on. This contract is **backward-compatible** with the current script for the parts the workflow parses (markers, oras push); it changes only the internal provisioning behavior.

## Invocation

- **Executed by**: the Remote Executor, inside the hardened execution container, as user `65534:65534`.
- **Shell**: `bash`, `set -euo pipefail`.
- **CWD**: unspecified; the script must not depend on a writable CWD.

## Inputs (environment variables)

| Var | Required | Meaning |
|---|---|---|
| `GITHUB_TOKEN` | yes | GHCR auth (passed via `--script-env`) |
| `GITHUB_REPOSITORY` | yes | `owner/repo` — GHCR package path |
| `COMMIT_SHA` | yes | Commit being built — unique tag component |
| `BUILD_SCRATCH_DIR` | no | Scratch root; **defaults to `/tmp`** |

Missing any required var → non-zero exit with a message naming the missing var (existing `: "${VAR:?…}"` behavior).

## Preconditions (provided by the image, NOT installed at run time)

- `cargo`, `rustc`, `cc`, `curl`, `oras` are present on `PATH` and runnable by `65534`.
- The script performs a **preflight** `command -v` for each; a missing tool → exit non-zero with `required tool not found: <name>` (FR-016).

## Filesystem contract

- Root filesystem: **read-only** — script writes nothing to it (FR-008).
- `/workspace`: **read-only** — script reads source from `/workspace/rust-project` and **copies** it into scratch; never writes under `/workspace` (FR-009).
- Scratch (`$BUILD_SCRATCH_DIR`, default `/tmp`): the **only** writable location. All of the following live beneath it (FR-010, FR-010a):
  - `CARGO_HOME` = `$SCRATCH/.cargo`
  - `RUSTUP_HOME` = read-only image path (set but not written; see research R1)
  - `CARGO_TARGET_DIR` = `$SCRATCH/target`
  - source copy = `$SCRATCH/rust-project`
  - oras auth = `$SCRATCH/oras-auth.json`

## Behavior / ordering

1. Preflight: required env vars + required tools + scratch writable (write-probe).
2. Stage `/workspace/rust-project` → `$SCRATCH/rust-project`.
3. `cargo build --release` with `CARGO_HOME`/`CARGO_TARGET_DIR` in scratch.
4. Assert binary exists at `$SCRATCH/target/release/attested-hello`.
5. Compute SHA-256 over the complete binary.
6. `oras login` + `oras push` the binary to a temporary GHCR package.
7. **Only on full success**, emit markers (step 8).

## Outputs (stdout markers — UNCHANGED, FR-013 / SC-007)

```
BINARY_SHA256:<64-char-lowercase-hex>
BINARY_OCI_REF:<oci-reference>
```

- Format identical to today (workflow `grep -oP '^BINARY_SHA256:\K[0-9a-fA-F]{64}$'` etc. must keep matching).
- Diagnostics go to **stderr**; only the two markers (and any prior content the workflow tolerates) go to stdout.

## Failure contract (FR-016, FR-016a, FR-016b)

Every failure: **non-zero exit** + stderr message naming the **condition** and the **offending detail**:

| Condition | Message names | Markers emitted? |
|---|---|---|
| Missing tool | the tool (e.g. `oras`) | no |
| Write outside scratch | OS-enforced: read-only rootfs/workspace denies it (`EROFS`); the failing command's error (incl. the path) is surfaced — the script does not classify "outside scratch" | no |
| Scratch not writable (up-front) | the scratch path (script write-probe) | no |
| Scratch exhausted | that the writable scratch mount was filled | no |
| Compile/link failure | the failing build step | no |
| Blocked egress / push failure | the push/egress failure | no |

- No corrupt/partial artifact is produced or pushed (FR-016a): digest is over a complete binary; push is all-or-nothing; markers print only after a successful push.
- No privileged fallback on any failure (FR-016): the script never attempts to install/escalate.

## Explicitly removed vs. legacy

- ❌ "Step 0" system-dependency install (`apt-get`/`dnf`/`yum`/`apk`).
- ❌ rustup toolchain download/install at run time.
- ❌ oras tarball download at run time (oras ships in the image).
