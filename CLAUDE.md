For project context — capabilities, requirements, and design rationale — see the
OpenSpec specs under `openspec/specs/`:

- `hardened-build-environment/` — the Rust project, the pinned build image, and
  the build script that runs under the Remote Executor's hardened defaults.
- `attested-build-workflow/` — the GitHub Actions orchestration (dispatch →
  remote attested build → integrity verify → sign → publish → attest → cleanup).
- `attested-executor-caller/` — the attested-channel client and its verification
  logic.

Each capability has a `spec.md` (requirements as `Requirement`/`Scenario` blocks)
and a `design.md` (decisions, alternatives, and trade-offs).
