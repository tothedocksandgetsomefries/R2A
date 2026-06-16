# Changelog

## v0.1.1 - Unreleased

Patch release focused on release-readiness, Web UI stability, recovery behavior, and reviewer/status display consistency.

### Fixed

- Fixed source acquisition recognition for user-provided Code / Artifact URLs.
- Improved source hint priority and rejection of invalid or incomplete GitHub URLs.
- Fixed Reviewer `NEEDS_INPUT` alias normalization to avoid invalid verdict handling.
- Fixed REVIEW_FEEDBACK transaction verdict normalization.
- Clarified paper LOW_CONFIDENCE caption-only messaging so it does not imply exact plotted values were extracted.
- Fixed input integrity handling for paper-level, full-scale, and internal-corpus datasets so reference-only paper data does not become an incorrect hard blocker.
- Preserved the Reviewer safety gate: target-required blockers still reject L3/L4 pass-like verdicts.
- Fixed Web UI stage status mapping for `SUCCESS`, `APPROVED`, `NEEDS_FIX`, `NEEDS_INPUT`, reviewer safety failures, and `INPUT_CONTRACT_READY`.
- Fixed current-stage-aware Workflow Review stage bar display so a current iteration does not read stale prior-iteration Manager/Reviewer statuses.
- Fixed fresh startup active-run recovery so cancelled, stopped, failed, terminal, and stale `stopping` / `stop_requested` / `user_requested` runs do not lock the Run Workflow button.
- Fixed Final `completed_with_failure` / `cancelled` display paths so terminal failures are not shown as misleading green success.
- Improved Final report human narrative and Workflow Review report layout.

### Tests

- Added and updated active-run recovery tests for stale `active_run.json` pointers, terminal statuses, and Run Workflow button enablement.
- Added and updated UI stage bar tests for current-stage masking and input-contract-ready display.
- Added targeted coverage for reviewer verdict normalization, input integrity, source acquisition, and final report behavior.
