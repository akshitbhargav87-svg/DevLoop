# DevLoop 2.0 Roadmap

This roadmap prioritizes a small, demonstrable improvement to DevLoop's existing guarded repair workflow. It is a plan, not a claim that these features are implemented.

## Goal

Make repair outcomes easier to trust and compare by explicitly checking for regressions and running a repeatable set of known bug-repair tasks.

## P0: Preserve the current vertical slice

- Keep repository isolation, patch validation, human approval, test execution, and resolution reports working.
- Establish the current test suite and the existing intentional-bug demo as the baseline.
- Record any flaky or environment-dependent tests before adding features.

## P1: Regression-aware verification

- Capture baseline test results before applying a candidate patch.
- Run the same test command after the patch is applied.
- Report newly failing tests separately from failures that existed before the repair.
- Never mark a repair as verified when tests did not run or the result is ambiguous.
- Add tests for pass, fail, timeout, malformed output, and missing test-command cases.

## P1: Small reproducible benchmark

Start with 3–5 small, deterministic bug fixtures. Each fixture should include:
- A repository or minimal project fixture pinned to a known revision.
- A bug description and reproducible failing test.
- Expected behavior and a clear pass/fail criterion.
- A bounded test command and timeout.

Run each fixture through the same repair pipeline used by the UI. Store per-run results such as task success, regression-free success, attempts, duration, and test output. Record model/provider configuration where available.

Do not compare models or publish aggregate success rates until the same benchmark can be rerun under documented conditions.

## P2: Results UI

- Show the patch diff, pre/post test status, and final verification state.
- Add a compact benchmark table with fixture, status, attempts, duration, and regression result.
- Make unverified outcomes visibly distinct from successful outcomes.

## Safety and scope boundaries

- Keep patches in isolated workspaces; do not push changes to the source repository automatically.
- Preserve human approval before applying the final candidate patch.
- Keep test files read-only for the patch-generation step unless a fixture explicitly requires a test change.
- Use bounded execution time and output size for test commands.
- Do not add multi-agent competition, a large benchmark catalog, or unrelated integrations until the core flow is reliable.

## Suggested acceptance criteria

- Existing tests pass.
- The current end-to-end repair demo still works.
- At least three fixtures can be run repeatedly with structured results.
- A failed or skipped test cannot be reported as a verified repair.
- Benchmark results include enough context to reproduce each run.

## Suggested implementation order

1. Inspect the current workflow and test-result representation.
2. Add regression-aware pre/post test comparison.
3. Add deterministic fixture definitions and a benchmark runner.
4. Persist and expose benchmark results.
5. Add a small dashboard view and document measured results.
