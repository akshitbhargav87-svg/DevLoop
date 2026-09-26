# DevLoop — Technical Plan (v3, Final)

## Top-Level Overview

DevLoop is an autonomous bug-resolution workflow system built for the IBM Bob
2.0 Hackathon. A developer submits a structured bug report (title, description,
stack trace, Git repo URL). An orchestrator agent coordinates specialised
sub-agents that clone the repo, analyse logs and the repository tree in
**genuine parallel**, inspect recent changes, synthesise a root-cause
hypothesis, and generate a proposed fix. The workflow **pauses for explicit
developer approval** before applying the patch. After approval the patch is
applied locally, tests are run, and the system iterates (up to 3 times) if
tests fail. A final resolution report is produced.

**Stack:**
- Backend: Python 3.11, FastAPI, LangGraph, `ibm-watsonx-ai`, `langchain-ibm`
- LLM: IBM watsonx.ai — `ibm/granite-3-8b-instruct` via `ChatWatsonx`
- Frontend: React 18, TypeScript, Vite, Tailwind CSS
- Repo inspection: `gitpython`, `subprocess` (pytest)
- Storage: SQLite via synchronous SQLAlchemy (no migration tooling needed)
- Transport: REST + Server-Sent Events (SSE) for live agent step streaming

**Priorities:** end-to-end working workflow first; optional infrastructure
and extensive tests are secondary.

**Non-goals (deferred):**
- Run history page / persistent run list
- Authentication / multi-tenancy
- Cloud deployment
- Persistent vector store / RAG
- Automatic remote push of patches
- Raw LLM chain-of-thought in the UI

---

## Architecture

```
Browser (React / TypeScript)
  │
  │  POST /api/runs                   submit bug report → start workflow
  │  GET  /api/runs/{id}/events       SSE stream of AgentStepEvents
  │  GET  /api/runs/{id}              current run state + all steps
  │  POST /api/runs/{id}/approve      developer approves or rejects patch
  │  GET  /api/reports/{run_id}       final resolution report
  │
  ▼
FastAPI  (backend/)
  └── WorkflowService
        └── LangGraph StateGraph
              ├── clone_repo_node
              ├── ┌─────────────────────────────┐  (parallel fan-out)
              │   │ log_analysis_node            │
              │   │ file_identification_node     │
              │   └─────────────────────────────┘
              ├── change_inspection_node          (fan-in: receives both outputs)
              ├── hypothesis_node
              ├── patch_node
              ├── ── PAUSE: awaiting_approval ──  (human gate)
              ├── apply_patch_node                (local only — no remote push)
              ├── test_runner_node
              └── report_node
                    ↑ (loop to hypothesis_node on failure, max 3 iterations)
```

### Approval gate
After `patch_node` completes, the graph suspends via a LangGraph interrupt.
The run `status` is set to `awaiting_approval` in the DB. Resumption happens
only when the developer calls `POST /api/runs/{id}/approve`:
- `{"approved": true}` → graph resumes at `apply_patch_node`
- `{"approved": false}` → graph skips to `report_node` with `patch_approved = false`

The patch is applied **only** in the isolated temporary workspace. The tool
layer has no `git push` capability.

---

## Data Model

Tables are created via `Base.metadata.create_all()` on app startup.

### BugReport
| Field | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| title | TEXT | |
| description | TEXT | |
| stack_trace | TEXT | nullable |
| repo_url | TEXT | git clone URL or `file://` path |
| branch | TEXT | default `main` |
| created_at | TEXT | ISO datetime |

### WorkflowRun
| Field | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| bug_report_id | TEXT FK | → BugReport |
| status | TEXT | `pending \| running \| awaiting_approval \| approved \| rejected \| completed \| failed` |
| workspace_dir | TEXT | absolute path to temp clone |
| iteration | INT | default 0 |
| created_at | TEXT | |
| completed_at | TEXT | nullable |

### AgentStep
| Field | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| run_id | TEXT FK | → WorkflowRun |
| step_name | TEXT | |
| status | TEXT | `pending \| running \| completed \| failed` |
| evidence | TEXT | JSON — structured evidence object (not raw LLM output) |
| started_at | TEXT | |
| completed_at | TEXT | nullable |

**Evidence object shapes by step:**

| Step | Shape |
|---|---|
| `log_analysis` | `{exception, module, line, call_chain[]}` |
| `file_identification` | `{files: [{path, relevance_reason}]}` |
| `change_inspection` | `{commits: [{sha, message, files_changed[]}]}` |
| `hypothesis` | `{root_cause, affected_files[], fix_strategy, confidence}` |
| `patch` | `{diff, files_modified[], description}` |
| `test_runner` | `{passed, failed, errors[], output_excerpt}` |
| `report` | `{summary, evidence[], changes_made[], test_results}` |

### ResolutionReport
| Field | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| run_id | TEXT FK | → WorkflowRun |
| root_cause | TEXT | |
| evidence | TEXT | JSON list |
| changes_made | TEXT | JSON list of unified diffs |
| test_results | TEXT | JSON |
| iteration_count | INT | |
| patch_approved | INT | 0 = rejected/skipped, 1 = approved |
| generated_at | TEXT | |

---

## Agent Workflow — LangGraph StateGraph

### Shared State
```python
class WorkflowState(TypedDict):
    run_id:            str
    bug_report:        dict          # BugReport fields
    workspace_dir:     str
    log_analysis:      dict          # structured evidence from LogAnalysisAgent
    relevant_files:    dict          # structured evidence from FileIdentificationAgent
    recent_changes:    dict          # structured evidence from ChangeInspection
    hypothesis:        dict          # {root_cause, affected_files, fix_strategy, confidence}
    patch_diff:        str
    patch_description: str
    patch_approved:    bool | None   # None = not yet decided
    test_output:       dict          # {passed, failed, errors, output_excerpt}
    test_passed:       bool
    iteration:         int
    previous_failures: list[str]     # test failure summaries used during re-hypothesis
```

### Parallel Architecture

`log_analysis_node` and `file_identification_node` are **independent** —
neither depends on the other's output. Both receive only:
- The bug report (title, description, stack trace)
- The repository workspace (available after `clone_repo_node`)

`log_analysis_node` receives: `bug_report.stack_trace` + `bug_report.description`
`file_identification_node` receives: `bug_report.stack_trace` + output of `list_files(workspace)`

Both run as concurrent LangGraph branches (LangGraph `Send` / fan-out edges
from `clone_repo_node` to both, fan-in at `change_inspection_node`).

`change_inspection_node` receives **both** outputs as input: it uses the file
list from `file_identification` to target `git log` and `git show` calls, and
the exception/line info from `log_analysis` to focus the summary.

### Node Summary

| Node | Type | Parallel? | Responsibility |
|---|---|---|---|
| `clone_repo` | Tool | — | Clone repo into temp dir; populate `workspace_dir` |
| `log_analysis` | LLM sub-agent | ✓ with `file_id` | Parse stack trace → structured exception evidence |
| `file_identification` | LLM sub-agent | ✓ with `log_analysis` | Inspect repo tree → ranked file list |
| `change_inspection` | Tool + LLM | — (fan-in) | `git log` + diffs on relevant files; summarise |
| `hypothesis` | LLM orchestrator | — | Synthesise all evidence → root-cause + fix strategy |
| `patch` | LLM sub-agent | — | Read source files → produce unified diff |
| `awaiting_approval` | Human gate | — | Suspend; wait for `POST /approve` |
| `apply_patch` | Tool | — | Apply diff locally; no `git push` |
| `test_runner` | Tool | — | Run `pytest`; return structured results |
| `report` | LLM sub-agent | — | Assemble final ResolutionReport |

### Iteration loop
```
test_runner → test_passed?
  YES → report  (workflow complete)
  NO  → iteration < 3?
          YES → append failure to previous_failures → hypothesis (re-analyse)
          NO  → report  (workflow complete, unresolved)
```

### SSE events emitted
```json
{ "type": "step_started",   "step_name": "log_analysis" }
{ "type": "step_completed", "step_name": "log_analysis", "evidence": { ... } }
{ "type": "patch_ready",    "diff": "...", "description": "..." }
{ "type": "run_completed",  "report_id": "..." }
{ "type": "run_failed",     "error": "..." }
```

---

## Sub-Agent Responsibilities

### LogAnalysisAgent
- **Inputs:** `stack_trace`, `description`
- **Task:** Identify exception type, failing function, file, line numbers, and call chain
- **Output:** `{exception, module, line, call_chain[]}`
- **Independence:** Does NOT use the repo tree or file contents

### FileIdentificationAgent
- **Inputs:** `stack_trace`, recursive file listing from `list_files(workspace)`
- **Task:** Rank files by likely involvement; provide one-line rationale per file
- **Output:** `{files: [{path, relevance_reason, rank}]}`
- **Independence:** Does NOT use LogAnalysis output; operates purely on the repo structure and raw stack trace text

### HypothesisAgent
- **Inputs:** `log_analysis`, `relevant_files`, `recent_changes`, `previous_failures` (on retry)
- **Task:** Synthesise all evidence into a root-cause statement and fix strategy
- **Output:** `{root_cause, affected_files[], fix_strategy, confidence}`

### PatchAgent
- **Inputs:** `hypothesis`, file contents fetched via `read_file` tool
- **Task:** Produce a minimal unified diff implementing the fix strategy
- **Output:** `{diff, files_modified[], description}`

### ReportAgent
- **Inputs:** full `WorkflowState`
- **Task:** Assemble structured ResolutionReport; no padding
- **Output:** ResolutionReport fields

---

## Demo Scenario

### The Bug

An order/payment application charges customers the discount amount instead of
the discounted total.

| | Value |
|---|---|
| Order total | £100 |
| Coupon rate | 0.25 (25%) |
| Expected charge | £75.00 |
| Actual (buggy) charge | £25.00 |

The existing test asserts `amount == 75.0` and **fails before DevLoop runs**.
DevLoop does **not** create any tests. After the patch is approved and applied,
the existing test passes.

### Synthetic repo: `demo-target/`

**`store/pricing.py`** (buggy state — as it exists when DevLoop clones it)
```python
def calculate_discount(order_total: float, coupon_rate: float) -> float:
    """Return the discounted price for an order."""
    return order_total * coupon_rate          # BUG: returns discount amount, not final price
```

**`store/checkout.py`**
```python
from store.pricing import calculate_discount

def process_payment(order_total: float, coupon_rate: float = 0.0) -> dict:
    """Charge the customer the correct post-discount amount."""
    amount_to_charge = calculate_discount(order_total, coupon_rate)
    return {"status": "charged", "amount": amount_to_charge}
```

**`tests/test_checkout.py`** (committed before DevLoop runs; asserts the
correct £75 result — this test FAILS against the buggy code)
```python
from store.checkout import process_payment

def test_payment_with_coupon():
    result = process_payment(order_total=100.0, coupon_rate=0.25)
    assert result["amount"] == 75.0, f"Expected 75.0, got {result['amount']}"

def test_payment_no_coupon():
    result = process_payment(order_total=100.0, coupon_rate=0.0)
    assert result["amount"] == 100.0
```

### Git history of `demo-target` (3 commits, fixed and reproducible)

| # | Message | Change |
|---|---|---|
| 1 | `init: add pricing and checkout modules` | `apply_coupon()` returns `total * (1 - rate)` — correct |
| 2 | `refactor: rename apply_coupon to calculate_discount` | Formula accidentally changed to `total * rate` — **introduces bug** |
| 3 | `test: add checkout tests` | Adds `tests/test_checkout.py` asserting `amount == 75.0` — test fails |

### Expected agent trace

1. **LogAnalysisAgent** (parallel) — identifies no exception is thrown;
   identifies wrong numeric result; points to `pricing.py:3` and `checkout.py:5`
2. **FileIdentificationAgent** (parallel) — ranks `pricing.py` > `checkout.py` > `tests/test_checkout.py`
3. **ChangeInspection** — finds commit 2 "refactor: rename apply_coupon to
   calculate_discount"; diff shows formula change from `total * (1 - rate)` to `total * rate`
4. **HypothesisAgent** — root cause: rename refactor dropped `(1 - ...)` wrapper;
   fix strategy: restore `order_total * (1 - coupon_rate)` in `pricing.py`
5. **PatchAgent** — diff changes line 3 of `pricing.py` only
6. **ApprovalGate** — developer reviews one-line diff in the UI; clicks Approve
7. **TestRunner** — both existing tests pass (`test_payment_with_coupon` now returns 75.0)
8. **ReportAgent** — structured report: root cause, evidence from commit 2, diff, test results

### The correct patch (expected output)
```diff
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,3 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
     """Return the discounted price for an order."""
-    return order_total * coupon_rate
+    return order_total * (1 - coupon_rate)
```

### Pre-filled bug report (Load Demo)
- **Title:** `process_payment charges wrong amount when coupon is applied`
- **Description:** `When a 25% coupon is applied to a £100 order the customer is charged £25 instead of £75. The checkout test asserts 75.0 but receives 25.0.`
- **Stack trace:**
```
FAILED tests/test_checkout.py::test_payment_with_coupon
AssertionError: Expected 75.0, got 25.0
  File "tests/test_checkout.py", line 4, in test_payment_with_coupon
    assert result["amount"] == 75.0, f"Expected 75.0, got {result['amount']}"
  File "store/checkout.py", line 5, in process_payment
    amount_to_charge = calculate_discount(order_total, coupon_rate)
  File "store/pricing.py", line 3, in calculate_discount
    return order_total * coupon_rate
```
- **Repo URL:** `file:///absolute/path/to/DevLoop/demo-target`
- **Branch:** `main`

---

## Frontend

### Views
1. **Submit** (`/`) — Bug report form + "Load Demo" button
2. **Run Detail** (`/runs/:id`) — Live workflow view
3. **Report** (`/runs/:id/report`) — Final resolution report

### Run Detail layout

```
┌─────────────────────────────────────────────────────────────┐
│  Bug: "process_payment charges wrong amount..."             │
│  Status badge   Repo   Branch                               │
├────────────────────────┬────────────────────────────────────┤
│  STEP TIMELINE         │  DETAIL PANEL                      │
│                        │                                    │
│  ✓ Clone repo          │  [Selected step EvidenceCard]      │
│  ✓ Log analysis   ─┐   │                                    │
│  ✓ File ID        ─┘   │  Evidence rendered as labelled     │
│  ✓ Change inspect      │  key-value cards — no raw LLM text │
│  ✓ Hypothesis          │                                    │
│  ✓ Patch               │  ┌──────────────────────────────┐  │
│  ⏸ Awaiting approval  │  │  PATCH APPROVAL PANEL        │  │
│    Apply patch         │  │  One-line description        │  │
│    Test runner         │  │  Syntax-highlighted diff     │  │
│    Report              │  │  [Approve]  [Reject]         │  │
│                        │  └──────────────────────────────┘  │
└────────────────────────┴────────────────────────────────────┘
```

`log_analysis` and `file_identification` are shown as a parallel pair in the
timeline with a visual bracket to indicate they ran concurrently.

### Key Components
| Component | Responsibility |
|---|---|
| `BugReportForm` | Controlled form; "Load Demo" pre-fills all fields |
| `StepTimeline` + `StepCard` | Ordered step list; clicking selects detail panel |
| `EvidenceCard` | Renders any evidence shape as labelled key-value pairs |
| `DiffViewer` | Syntax-highlighted unified diff |
| `PatchApprovalPanel` | Diff + description + Approve/Reject; visible only when `awaiting_approval` |
| `TestResultPanel` | Pass/fail counts + output excerpt |
| `ReportView` | Structured ResolutionReport fields + copy-as-markdown |

### SSE hook
`useRunEvents(runId)` uses the native `EventSource` API. It returns a
live-updating array of `AgentStep` objects and the current run status.
The hook closes the connection when status reaches a terminal state.

---

## Test Strategy

### Priority
End-to-end integration correctness takes priority over unit test coverage
for the hackathon. Unit tests cover only the tools layer and a smoke test
for the workflow state machine.

### Backend (`pytest tests/`)
- `tests/test_tools.py` — repo, git, and test tools against the local `demo-target` fixture
- `tests/test_workflow.py` — full LangGraph state machine with all LLM nodes
  mocked; verify parallel fan-out, approval gate, and iteration loop

### Frontend (`vitest` + `@testing-library/react`)
- `BugReportForm` — renders, pre-fills on "Load Demo", submits
- `PatchApprovalPanel` — visible only when status is `awaiting_approval`

### Integration (manual)
- End-to-end run against `demo-target` with real watsonx.ai credentials
- Confirm final report root cause matches "formula changed in rename refactor"

---

## Local Setup

### Prerequisites
Python 3.11+, Node.js 20+, Git

### Environment (`backend/.env`)
```
WATSONX_API_KEY=...
WATSONX_PROJECT_ID=...
WATSONX_URL=https://us-south.ml.cloud.ibm.com
WATSONX_MODEL_ID=ibm/granite-3-8b-instruct
DEMO_REPO_PATH=../demo-target
```

### Backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev       # Vite on :5173, /api proxied to :8000
```

---

## Repository Structure

```
DevLoop/
  devloop-plan.md
  README.md
  demo-target/                      synthetic bug repo (locally git-init'd)
    store/
      __init__.py
      pricing.py                    buggy formula
      checkout.py
    tests/
      __init__.py
      test_checkout.py              asserts 75.0 — fails before patch
    README.md
  backend/
    main.py                         FastAPI app, CORS, startup
    config.py                       Pydantic BaseSettings
    database.py                     SQLAlchemy sync engine + Base
    models/
      __init__.py
      bug_report.py
      workflow_run.py
      agent_step.py
      resolution_report.py
    routers/
      runs.py                       POST/GET runs, SSE, approve
      reports.py                    GET report
    services/
      workflow_service.py           run_workflow + resume_workflow
    agents/
      orchestrator.py               WorkflowState + StateGraph
      log_analysis.py
      file_identification.py
      hypothesis.py
      patch.py
      report_agent.py
    tools/
      repo_tools.py                 clone, list_files, read_file, grep
      git_tools.py                  recent_commits, show_commit_diff
      test_tools.py                 apply_patch, run_pytest
    watsonx_client.py
    requirements.txt
  frontend/
    index.html
    vite.config.ts
    tailwind.config.ts
    src/
      App.tsx
      main.tsx
      api/
        client.ts
        runs.ts
        reports.ts
      hooks/
        useRunEvents.ts
      pages/
        Home.tsx
        RunDetail.tsx
        Report.tsx
      components/
        BugReportForm.tsx
        StepTimeline.tsx
        StepCard.tsx
        EvidenceCard.tsx
        DiffViewer.tsx
        PatchApprovalPanel.tsx
        TestResultPanel.tsx
        ReportView.tsx
      types/
        index.ts
    package.json
  tests/
    test_tools.py
    test_workflow.py
```

---

## Sub-Tasks

### ST-01 — Demo target repository
**Intent:** Create the synthetic order/payment Python project with a fixed
3-commit history. The existing test asserts the correct result (75.0) and
must fail against the buggy code before DevLoop runs, then pass after the
patch is applied.

**Expected outcomes:**
- `demo-target/` is a git repo with exactly 3 commits as described above
- `pytest demo-target` produces: 1 passed (`test_payment_no_coupon`), 1 failed (`test_payment_with_coupon`)
- The failure message reads: `AssertionError: Expected 75.0, got 25.0`
- After manually applying the one-line patch, both tests pass

**Todo:**
- [ ] Create `demo-target/store/__init__.py`, `pricing.py` (correct formula), `checkout.py`
- [ ] `git init --initial-branch=main`; commit 1: "init: add pricing and checkout modules"
- [ ] Change `pricing.py` formula to the buggy `total * rate`; commit 2: "refactor: rename apply_coupon to calculate_discount"
- [ ] Create `demo-target/tests/__init__.py` and `test_checkout.py` asserting `amount == 75.0`; commit 3: "test: add checkout tests"
- [ ] Verify `pytest demo-target/tests` gives 1 passed + 1 failed with the correct message
- [ ] Add `demo-target/README.md`

**Relevant context:** The test file is committed in the buggy state. DevLoop must
not create or modify tests during the workflow. The patch touches only `pricing.py`.

**Status:** [ ] pending

---

### ST-02 — Backend skeleton
**Intent:** Stand up FastAPI, SQLite, and config so subsequent sub-tasks have
a running server.

**Expected outcomes:**
- `GET /health` returns `{"status": "ok"}`
- All four DB tables created on startup
- `config.py` loads all `.env` values

**Todo:**
- [ ] Create `backend/config.py` (Pydantic `BaseSettings`)
- [ ] Create `backend/database.py` (sync SQLAlchemy engine, `Base`, `get_db`)
- [ ] Create `backend/models/` — four model files matching the data model section
- [ ] Create `backend/main.py` — app factory, CORS, `/health`, `create_all()` on startup
- [ ] Write `backend/requirements.txt`: fastapi, uvicorn, sqlalchemy, pydantic-settings, gitpython, ibm-watsonx-ai, langchain-ibm, langgraph, python-dotenv, sse-starlette, pytest

**Status:** [ ] pending

---

### ST-03 — Backend tools layer
**Intent:** Implement all deterministic tool functions (no LLM) used by agent
nodes. These are the lowest-level primitives; all other backend work builds on them.

**Expected outcomes:**
- `clone_repo(url, branch)` → temp dir path
- `list_files(workspace)` → recursive file listing string
- `read_file(workspace, rel_path)` → file content
- `grep_symbol(workspace, symbol)` → list of `{file, line, text}`
- `recent_commits(workspace, n)` → list of `{sha, message, author, date}`
- `show_commit_diff(workspace, sha)` → unified diff string
- `apply_patch(workspace, diff_str)` → success bool; no `git push`
- `run_pytest(workspace)` → `{passed, failed, errors[], output_excerpt}`
- `tests/test_tools.py` passes using `demo-target` as fixture

**Todo:**
- [ ] Implement `backend/tools/repo_tools.py`
- [ ] Implement `backend/tools/git_tools.py`
- [ ] Implement `backend/tools/test_tools.py`
- [ ] Write `tests/test_tools.py`

**Status:** [ ] pending

---

### ST-04 — watsonx.ai client and sub-agents
**Intent:** Implement the `ChatWatsonx` factory and all five LLM sub-agents.
Each agent returns only structured evidence — no raw LLM text — and is callable
as a standalone function for easy testing and mocking.

**Expected outcomes:**
- `watsonx_client.py` builds `ChatWatsonx` from config
- `log_analysis.py` — input: `stack_trace + description`; output: `{exception, module, line, call_chain[]}`
- `file_identification.py` — input: `stack_trace + list_files output`; output: `{files: [{path, relevance_reason, rank}]}`; **no dependency on log_analysis output**
- `hypothesis.py` — input: all prior evidence + `previous_failures`; output: `{root_cause, affected_files[], fix_strategy, confidence}`
- `patch.py` — input: hypothesis + file contents; output: `{diff, files_modified[], description}`
- `report_agent.py` — input: full state; output: ResolutionReport fields
- All agents enforce JSON-only output via prompt; fall back gracefully on malformed LLM responses

**Todo:**
- [ ] Implement `backend/watsonx_client.py`
- [ ] Implement each of the five agent files
- [ ] Add JSON output enforcement to all prompts
- [ ] Add a safe JSON-parse helper that returns a fallback dict on failure

**Status:** [ ] pending

---

### ST-05 — LangGraph orchestrator with approval gate
**Intent:** Wire all nodes into a LangGraph `StateGraph` with genuine parallel
fan-out for `log_analysis` and `file_identification`, the human-in-the-loop
approval interrupt, and the iteration loop. Each node emits SSE events.

**Expected outcomes:**
- `log_analysis_node` and `file_identification_node` run as concurrent branches
  from `clone_repo_node` (LangGraph `Send` fan-out); neither waits for the other
- `change_inspection_node` receives both outputs and proceeds only after both complete
- After `patch_node`, graph suspends; `run.status` set to `awaiting_approval`
- `resume_workflow(run_id, approved)` correctly resumes or short-circuits to report
- Iteration loop re-enters `hypothesis_node` with `previous_failures` appended; capped at 3
- Per-run `asyncio.Queue` emits structured SSE events at each node transition
- `tests/test_workflow.py` verifies the state machine with all LLM nodes mocked

**Todo:**
- [ ] Define `WorkflowState` TypedDict in `agents/orchestrator.py`
- [ ] Implement all graph nodes as thin wrappers calling agent/tool functions
- [ ] Wire `clone_repo` → fan-out to both `log_analysis` and `file_identification` using LangGraph `Send`
- [ ] Wire fan-in from both parallel nodes into `change_inspection`
- [ ] Implement approval interrupt using LangGraph `MemorySaver` checkpoint
- [ ] Implement `resume_workflow(run_id, approved: bool)` in `workflow_service.py`
- [ ] Implement per-run `asyncio.Queue` event bus
- [ ] Add conditional edge for iteration loop
- [ ] Write `tests/test_workflow.py`

**Status:** [ ] pending

---

### ST-06 — FastAPI routers and SSE
**Intent:** Expose all REST and SSE endpoints the frontend needs.

**Expected outcomes:**
- `POST /api/runs` — creates BugReport + WorkflowRun, starts workflow in background task, returns `{run_id}`
- `GET  /api/runs/{id}` — returns run status + ordered list of AgentSteps with evidence
- `GET  /api/runs/{id}/events` — SSE stream; pushes events until terminal state
- `POST /api/runs/{id}/approve` — body `{"approved": bool}`; calls `resume_workflow`; returns updated status
- `GET  /api/reports/{run_id}` — returns ResolutionReport

**Todo:**
- [ ] Implement `backend/routers/runs.py`
- [ ] Implement `backend/routers/reports.py`
- [ ] Wire SSE endpoint to per-run `asyncio.Queue`
- [ ] Register routers and add CORS in `main.py`

**Status:** [ ] pending

---

### ST-07 — Frontend scaffold, types, and API layer
**Intent:** Bootstrap the React/TypeScript/Vite/Tailwind app and implement the
typed API client and SSE hook so page components can be built independently.

**Expected outcomes:**
- `npm run dev` starts; `/api` proxied to `:8000`
- `types/index.ts` exactly mirrors backend models
- `useRunEvents(runId)` returns live-updating step array via SSE; closes on terminal status
- Vite proxy configured

**Todo:**
- [ ] Scaffold with `npm create vite@latest frontend -- --template react-ts`
- [ ] Install: `tailwindcss @tailwindcss/vite axios react-router-dom`
- [ ] Configure Vite proxy
- [ ] Write `src/types/index.ts`
- [ ] Implement `src/api/client.ts`, `runs.ts`, `reports.ts`
- [ ] Implement `src/hooks/useRunEvents.ts`

**Status:** [ ] pending

---

### ST-08 — Frontend pages and components
**Intent:** Build all UI views. Evidence is always rendered via `EvidenceCard`.
The `PatchApprovalPanel` is the centrepiece of the demo and must be prominent.

**Expected outcomes:**
- Home: form renders; "Load Demo" pre-fills all fields correctly; submit navigates to `/runs/:id`
- RunDetail: step timeline updates live; clicking a step shows evidence in the detail panel; parallel steps shown with visual bracket; `PatchApprovalPanel` visible only when `awaiting_approval`; Approve/Reject call `POST /api/runs/{id}/approve`
- Report: all ResolutionReport fields rendered in structured form
- Layout is clean at 1280px width

**Todo:**
- [ ] Implement `BugReportForm.tsx` with "Load Demo" pre-fill using demo scenario values
- [ ] Implement `StepTimeline.tsx` + `StepCard.tsx` (parallel steps shown as a pair)
- [ ] Implement `EvidenceCard.tsx` (handles all evidence shapes)
- [ ] Implement `DiffViewer.tsx`
- [ ] Implement `PatchApprovalPanel.tsx`
- [ ] Implement `TestResultPanel.tsx`
- [ ] Implement `ReportView.tsx`
- [ ] Implement `pages/Home.tsx`, `RunDetail.tsx`, `Report.tsx`
- [ ] Set up React Router in `App.tsx`

**Status:** [ ] pending

---

### ST-09 — Integration, demo validation, and README
**Intent:** Validate the full end-to-end demo and produce a README so judges
can run the demo in under 5 minutes.

**Expected outcomes:**
- `pytest tests/` passes
- Full demo run: submit pre-filled bug report → watch 8 steps (2 in parallel) → approve patch → both tests pass → report names correct root cause
- `README.md` covers prerequisites, `.env` setup, start commands, and a 3-minute demo script

**Todo:**
- [ ] Run `pytest tests/` — fix any failures
- [ ] Run full end-to-end demo with real watsonx.ai credentials
- [ ] Verify ResolutionReport names "rename refactor" + `pricing.py` formula as root cause
- [ ] Verify both `test_payment_no_coupon` and `test_payment_with_coupon` pass after patch
- [ ] Fix any integration issues
- [ ] Write `README.md`

**Status:** [ ] pending
