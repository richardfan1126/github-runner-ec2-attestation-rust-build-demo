# Contract: Image-Publish CI Workflow (`.github/workflows/build-image.yml`)

Builds the checked-in `Dockerfile`, publishes to this repo's GHCR namespace, and surfaces the immutable digest (FR-005, FR-005a).

## Trigger

- `workflow_dispatch` (manual). MAY also trigger on `push` affecting `Dockerfile` or `scripts/build-rust.sh`.

## Permissions

```yaml
permissions:
  contents: read
  packages: write   # push to GHCR
```

## Steps (contract)

1. Checkout.
2. Log in to `ghcr.io` using `GITHUB_TOKEN`.
3. Build `Dockerfile` for `linux/amd64` and push to `ghcr.io/${{ github.repository }}/build-image:<tag>` (e.g. tag = short SHA and/or `latest`).
4. **Capture the pushed image digest** (e.g. `docker/build-push-action` `outputs.digest`).
5. Write the immutable reference `ghcr.io/${{ github.repository }}/build-image@<digest>` to `$GITHUB_STEP_SUMMARY` so operators can copy the digest to pin (FR-017).

## Outputs

| Output | Where | Consumer |
|---|---|---|
| Immutable image reference `…@sha256:<digest>` | Job summary (+ optionally a step output) | Operator (pins the executor's build image) |

## Out of scope

- No provenance/attestation of the image itself (US2 clarification, FR-005a).
- Does not modify or invoke the Remote Executor.
