# R2A Protocol

This protocol is the shared contract for R2A stages. Stage prompts may add
local detail, but they must not weaken these rules.

## Default Reproduction Goal

R2A does not default to full-paper reproduction. Its default goal is to
determine how far the paper can be reproduced with trustworthy evidence and,
when evidence allows, advance through L0-L4:

- Default target range: L0, L1, L2, L3, L4.
- Optional enhancement: L5.
- Manual budget-gated target: L6.

No stage may describe smoke tests or partial engineering
checks as full paper reproduction.

## L0-L6 Evidence Progression

### L0_project_health

- Target project health check.
- Prefer finding and running the target project's test command.
- If no test command exists, write `NO_TEST_COMMAND_FOUND`.
- Does not represent paper reproduction.

### L1_source_artifact_verified

- Official source, artifact, commit, README, build docs, and experiment scripts
  are verified.
- May include build/import/runtime smoke evidence.
- Does not represent paper experiment reproduction.

### L2_input_contract_ready

- Dataset, query files, ground truth, database/index inputs, filter
  predicates/metadata, parameters, metric definitions, and run commands are
  verified as an input contract.
- If key inputs are missing, stop at L2 with `NEEDS_INPUT`.
- Synthetic or self-created data must not be presented as official input.

### L3_official_reduced_run

L3 requires all of the following:

- Official source or trustworthy paper artifact verified.
- Commit, branch, or tag recorded.
- At least one auditable build/import/runtime smoke evidence item.
- Official or paper-linked reduced input, small sample, or lightweight subset.
- Dataset, query files, ground truth, method, parameters, metric definition,
  and `k` are explicit.
- Filter predicates, metadata, and selectivity settings are explicit when the
  paper method requires them.
- The paper method is actually run on the reduced input.
- Measured metrics are written to `.r2a/results/reduced_metrics.csv`.
- Command provenance links every measured row to command evidence:
  `command_id`, `command`, `exit_code`, `duration_sec`, `log_path`,
  `artifact_path` or `artifact_hash`, and input provenance.

The following cannot count as L3: mock run, import test, build test, smoke test,
or an AI-written unofficial implementation.

### L4_reduced_paper_aligned

L4 requires L3 plus explicit paper alignment:

- `reduced_metrics.csv` exists and is schema-valid.
- `command_manifest.csv` or equivalent provenance exists.
- The reduced run is mapped to a paper table, figure, or experiment setting.
- Alignment evidence is recorded in `.r2a/results/paper_alignment.csv`.
- A user-facing `.r2a/results/L4_ALIGNMENT_SUMMARY.md` should summarize the
  mapping, metrics, provenance, limitations, and next step when L4 evidence is
  present.
- Final Report and Reviewer output should include an explicit paper-alignment
  summary.
- Differences are stated for dataset scale, hardware, runtime budget,
  parameters, number of repeats, baselines included or missing, metric
  definition, input source, and known evidence gaps.

Recommended `paper_alignment.csv` schema:

```csv
paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes
```

Allowed `match_status` values:

- `MATCH`
- `PARTIAL_MATCH`
- `MISMATCH`
- `NOT_AVAILABLE`
- `NEEDS_HUMAN_VERIFICATION`

New `paper_alignment.csv` rows must not use legacy `PARTIAL` or `GAP` in
`match_status`. Use `PARTIAL_MATCH` for partial alignment and `NOT_AVAILABLE`
when the reduced setting is missing or cannot be aligned.

L4 is still reduced paper-aligned evidence, not full reproduction.
It may claim only: `Reduced paper-aligned evidence with limitations.`

## Cases That Must Not Enter L3/L4

The following cap the run at L2 or lower unless the missing evidence is fixed:

- No official source or trustworthy artifact.
- Only an AI-written reimplementation.
- Only build/import/smoke evidence.
- Missing query files.
- Missing ground truth, unless bounded brute-force ground truth is explicitly
  valid for the reduced official input.
- Unclear metric definition.
- Training papers without checkpoint, config, data, or eval script.
- Large training, download, storage, runtime, or multi-GPU requirements without
  explicit user budget authorization.
- Results without command provenance.
- Results that cannot be mapped to a paper experiment.
- Unofficial datasets or self-created inputs without `DEMO_ONLY` or
  `REIMPLEMENTATION_ONLY` labels.

## Required CSV Artifacts

- `reduced_metrics.csv` is reserved for real official or paper-linked reduced
  runs. Rows must include at least `command_id`, `dataset`, `method`, `k`, and
  `notes`; measured metric columns are paper-dependent and may include
  `recall`, `qps`, `latency_ms`, `selectivity`, `efSearch`, `build_time`, or
  `index_size`.
- `command_manifest.csv` schema:
  `command_id,command,exit_code,duration_sec,log_path,artifact_path,artifact_hash,input_provenance,notes`.
- For cloned source artifacts, source and command provenance must use the
  actual local checkout: `git -C <artifact_repo_path> rev-parse HEAD`,
  `git -C <artifact_repo_path> remote get-url origin`, and
  `git -C <artifact_repo_path> rev-parse --abbrev-ref HEAD`. Expected commits
  from paper text, Planner notes, or old state are not actual provenance unless
  they match the checkout HEAD.
- Every `command_id` in `reduced_metrics.csv` must be present in
  `command_manifest.csv` unless an explicitly equivalent provenance mechanism
  is documented and accepted by Reviewer.
- `paper_alignment.csv` schema:
  `paper_item,setting_name,paper_setting,reduced_setting,match_status,evidence_source,notes`.
  `paper_item`, `setting_name`, and `evidence_source` must be non-empty. At
  least one `MATCH` or `PARTIAL_MATCH` row is required before L4 can be
  considered achieved.

### L5_minimal_baseline_comparison

- Optional enhancement after L4.
- Run only when a low-cost baseline is available on the same reduced input.
- Not the default automatic goal.

### L6_full_or_near_full_reproduction

- Requires explicit user authorization for compute, storage, dataset access,
  runtime, and baseline matrix.
- Default automation must not advance to L6.

## Contract Modes

- `verification_only`: verify source, build/runtime smoke, inputs, blockers,
  and provenance without claiming metrics.
- `smoke`: verify source/build/runtime harness behavior without claiming metrics.
- `official_reduced`: run an official reduced experiment only after L3 entry
  requirements are satisfied.
- `full_benchmark`: run only with explicit user approval and required resources.

## Verdict And Status Labels

Progress verdicts: `PASS_SMOKE_ONLY`, `INPUT_CONTRACT_READY`,
`PASS_REDUCED_METHOD_ONLY`, `PASS_REDUCED_ALIGNED`, `PASS_REDUCED_COMPARISON`.

Action verdicts: `NEEDS_FIX`, `NEEDS_INPUT`, `BORDERLINE`, `REJECT`.

Execution statuses: `PASS`, `FAIL`, `NOT_RUN`, `NEEDS_INPUT`.

## Failure Categories

- `SAFE_BUILD_COMPATIBILITY`
- `TOOLCHAIN_OR_ENVIRONMENT`
- `MISSING_ARTIFACT_OR_DATA`
- `API_OR_ALGORITHM_SEMANTICS`
- `RESULT_MISMATCH`
- `TIME_BUDGET`
- `TASK_AMBIGUITY`
- `RUNTIME_DLL_COMPATIBILITY`
- `ENGINEER_TIMEOUT_AFTER_BUILD`
- `DEMO_ONLY`

## Claim Restrictions

- L0/L1 do not claim paper reproduction.
- L2 claims only input-contract readiness.
- L3 may claim official reduced method evidence, not full reproduction.
- L4 may claim reduced paper-aligned evidence, not full reproduction.
- L5 may claim a minimal reduced baseline comparison, not full reproduction.
- L6 claims require explicit user budget authorization.
- Synthetic, mock, smoke, and reimplementation-only outputs must keep their
  labels and must not be written to `reduced_metrics.csv`.
