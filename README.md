
# DevLoop

**DevLoop is an autonomous software debugging and repair workflow that turns a bug report into a validated, human-approved code patch.**

It combines multi-agent reasoning, repository inspection, isolated workspaces, patch validation, automated tests, and human approval into one end-to-end workflow.

> **Hackathon project:** IBM Bob 2.0 Hackathon

---

## What DevLoop solves

Traditional debugging often requires a developer to manually move between stack traces, source files, Git history, test output, and patching.

DevLoop orchestrates those steps as a repeatable pipeline:

~~~text
Bug report
   ↓
Repository isolation
   ↓
Log analysis ─────────────┐
                          ├──→ Change inspection
File identification ─────┘
                                  ↓
                              Hypothesis
                                  ↓
                           Patch generation
                                  ↓
                        Pre-approval validation
                     (git apply --check + tests)
                                  ↓
                           Human patch review
                                  ↓
                        Apply patch in isolation
                                  ↓
                             Test & verify
                           ↙               ↘
                       pass               fail
                        ↓                   ↓
                    Report             retry loop
~~~

The system is designed so an LLM proposes a repair, while repository-aware validation and tests decide whether that proposal is safe enough to reach human review.

---

## Core workflow

### 1. Repository isolation

DevLoop accepts either:

- a local Git repository path during local development, or
- a Git repository URL.

The repository is cloned into an isolated workspace before analysis or patching.

### 2. Log analysis

The Log Analysis agent extracts useful failure information from supplied failure output.

Repository-aware validation prevents model-reported files or stack locations that do not exist in the cloned workspace from being trusted as authoritative evidence.

### 3. File identification

The File Identification agent identifies candidate files relevant to the reported failure.

Candidates are checked against the actual isolated repository before they are passed further into the workflow.

### 4. Change inspection

DevLoop inspects Git history and recent changes to understand what changed around the suspected failure.

### 5. Hypothesis generation

The Hypothesis agent combines:

- issue description,
- available failure output,
- identified files,
- current source,
- relevant tests,
- recent repository changes.

It produces a structured root-cause hypothesis and affected-file set.

### 6. Patch generation

The Patch agent proposes the smallest code change supported by the evidence.

Patch candidates must:

- declare at least one changed file,
- target files that actually exist in the workspace,
- match their declared file paths,
- be representable as a unified diff,
- treat tests as read-only evidence rather than implementation targets.

Malformed model output and missing optional failure information are handled without allowing the workflow to crash.

### 7. Pre-approval validation

Before a human can approve a patch, DevLoop validates it in a disposable copy of the isolated workspace.

Validation includes:

- patch-target validation,
- unified-diff checks,
- git apply --check,
- applying the candidate patch to a disposable copy,
- running the configured test command.

A candidate that fails validation is rejected and can be regenerated within the workflow retry loop.

### 8. Human approval

DevLoop pauses at **Patch Review**.

The user sees the generated patch and explanation before anything is applied.

Approval resumes the LangGraph workflow.

### 9. Apply and verify

After approval, the patch is applied to the isolated workspace and the configured tests are run again.

If verification fails, DevLoop can feed the failure back into the repair loop for another iteration.

### 10. Resolution report

The completed run records the root cause, evidence, changes made, test results, and iteration count in a resolution report.

---

## Architecture

~~~mermaid
flowchart TD
    A[Bug report] --> B[Repository isolation]
    B --> C[Log Analysis Agent]
    B --> D[File Identification Agent]
    C --> E[Change Inspection]
    D --> E
    E --> F[Hypothesis Agent]
    F --> G[Patch Agent]
    G --> H[Pre-approval validation]
    H --> I{Tests pass?}
    I -- No --> G
    I -- Yes --> J[Human Patch Review]
    J -- Reject --> K[Resolution Report]
    J -- Approve --> L[Apply Patch]
    L --> M[Test & Verify]
    M --> N{Verified?}
    N -- No --> G
    N -- Yes --> K
~~~

### Technology stack

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Vite |
| API | FastAPI |
| Workflow orchestration | LangGraph |
| LLM integration | LangChain |
| Local model runtime | Ollama |
| Local development model | Qwen qwen2.5-coder:1.5b |
| Optional hosted model | OpenAI-compatible provider |
| Persistence | SQLite |
| Git operations | Git / GitPython |
| Testing | pytest |
| Public deployment | Docker + Render |

---

## Agent roles

| Component | Responsibility |
|---|---|
| **Log Analysis Agent** | Extract and validate failure evidence |
| **File Identification Agent** | Find relevant repository files |
| **Change Inspection** | Inspect Git history and diffs |
| **Hypothesis Agent** | Synthesize root cause and affected files |
| **Patch Agent** | Generate a candidate unified diff |
| **Human Approval** | Review the proposed patch |
| **Apply Patch** | Apply only the approved patch to the isolated workspace |
| **Test & Verify** | Execute the configured test command |
| **Resolution Report** | Summarize the debugging run |

---

## Why the workflow is guarded

Autonomous code repair is only useful when the system can distinguish evidence from model guesses.

DevLoop therefore validates several boundaries:

- LLM-reported file paths are checked against the cloned repository.
- Patch declarations are checked against actual workspace files.
- Patch paths must match the files declared by the patch agent.
- Tests can be supplied as evidence without becoming patch targets.
- Candidate patches are tested on disposable copies before approval.
- Git applies the final patch using patch validation.
- The normal workflow operates on an isolated workspace rather than directly editing the source repository.

This reduces the blast radius of an incorrect model suggestion while preserving a fast, automated debugging loop.

---

## Local development

### Prerequisites

Install:

- Python 3.12+
- Node.js 22+
- Git
- Ollama

Verify Git:

~~~powershell
git --version
~~~

Verify Ollama:

~~~powershell
ollama --version
~~~

### 1. Clone DevLoop

~~~powershell
git clone https://github.com/akshitbhargav87-svg/DevLoop.git
cd DevLoop
~~~

### 2. Set up the backend

~~~powershell
cd backend

python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
~~~

### 3. Start Ollama and pull the local model

~~~powershell
ollama pull qwen2.5-coder:1.5b
~~~

Local development defaults to Ollama with the Qwen coding model. Configuration can be overridden through backend/.env.

### 4. Start the FastAPI backend

From backend/:

~~~powershell
.\\.venv\\Scripts\\python.exe -m uvicorn main:app --reload --port 8000
~~~

Health check:

~~~text
http://localhost:8000/health
~~~

Expected response:

~~~json
{"status":"ok"}
~~~

### 5. Start the frontend

Open a second terminal:

~~~powershell
cd frontend
npm install
npm run dev
~~~

Then open the Vite development URL, normally:

~~~text
http://localhost:5173
~~~

The Vite development server proxies /api requests to the local FastAPI server on port 8000.

---

## Running tests

From the repository root:

~~~powershell
python -m pytest -q
~~~

The finalized project was verified locally with **57 passing tests**.

The real-repository demo fixture was also verified end-to-end: DevLoop identified pricing.py, generated a patch for review, resumed after approval, applied the patch in the isolated workspace, and finished with:

~~~text
2 passed
~~~

---

## Using a local repository

During local development, the New Debug Run form supports a local Git repository.

Example:

~~~text
Repository type: Local
Repository path: C:\path\to\your\git-repository
Branch: main
Test command: pytest -q
~~~

Example issue:

~~~text
Title:
Coupon error

Description:
Coupon discount calculation is returning the wrong payment amount.
~~~

The repository is cloned into an isolated temporary workspace before the workflow operates on it.

---

## Using a Git repository URL

DevLoop also accepts Git repository URLs.

Example:

~~~text
Repository type: Git
Repository URL: https://github.com/example/project.git
Branch: main
Test command: pytest -q
~~~

For Git URL workflows, DevLoop works on the isolated clone. It does not automatically push changes back to the source repository.

This makes Git-hosted repositories convenient for demonstrations and safer for public deployments.

---

## API

The FastAPI backend exposes these main endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | /api/runs | Create a debugging run |
| GET | /api/runs/{run_id} | Get run status and step evidence |
| GET | /api/runs/{run_id}/events | Stream workflow events |
| POST | /api/runs/{run_id}/approve | Approve or reject a proposed patch |
| GET | /api/reports/{run_id} | Get the resolution report |
| GET | /api/auth/status | Get public-deployment authentication status |
| POST | /api/auth/login | Authenticate to a protected deployment |
| GET | /health | Health check |

### Example run payload

~~~json
{
  "title": "Coupon error",
  "description": "Coupon discount calculation is returning the wrong payment amount.",
  "repo_url": "https://github.com/example/devloop-real-test.git",
  "repository_type": "git",
  "branch": "main",
  "test_command": "pytest -q"
}
~~~

Approval payload:

~~~json
{
  "approved": true
}
~~~

---

## Configuration

DevLoop uses environment-based settings through pydantic-settings.

| Variable | Local default | Purpose |
|---|---|---|
| LLM_PROVIDER | ollama | Select the LLM integration |
| OLLAMA_MODEL | qwen2.5-coder:1.5b | Local Ollama model |
| OPENAI_BASE_URL | https://openrouter.ai/api/v1 | Hosted OpenAI-compatible endpoint |
| OPENAI_MODEL | openai/gpt-4o-mini | Hosted deployment model |
| OPENAI_API_KEY | unset | Hosted provider secret |
| DATABASE_URL | local SQLite | Workflow/report database |
| CHECKPOINT_PATH | local SQLite checkpoint file | LangGraph checkpoint storage |
| ALLOW_LOCAL_REPOSITORIES | true | Allow local filesystem repositories |
| ACCESS_TOKEN | unset | Optional deployment access code |
| STATIC_DIR | unset locally | Directory used to serve the built frontend |

Local secrets belong in backend/.env and should never be committed.

---

## Public deployment

DevLoop includes Docker and Render configuration for a single public web service.

Relevant files:

- Dockerfile
- render.yaml
- DEPLOY.md

The public deployment is intentionally different from local development:

- frontend and FastAPI backend are served together,
- local filesystem repositories are disabled,
- Git URLs are the supported repository source,
- an access token protects API operations,
- SQLite data and LangGraph checkpoints are stored on persistent disk,
- the deployment uses an OpenAI-compatible hosted model provider.

The included Render Blueprint defaults to OpenRouter with:

~~~text
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-4o-mini
~~~

Set OPENAI_API_KEY as a secret in the hosting platform. **Do not put API keys, access codes, or other secrets in Git.**

See [DEPLOY.md](DEPLOY.md) for deployment instructions.

---

## Security and safety model

### Repository isolation

Normal debugging operations run against a cloned or isolated workspace rather than directly modifying the developer's source repository.

### Patch validation

A generated patch must target real workspace files and pass Git patch validation before it can be approved.

### Test gating

Candidate patches are tested on disposable copies before the human review stage.

### Human-in-the-loop approval

A patch is not applied to the active isolated workflow until a user explicitly approves it.

### Public deployment restrictions

The public deployment disables local repository paths and requires authenticated API access.

### Secrets

Provider credentials and access tokens belong in environment variables or the hosting platform's secret store, not in the repository.

---

## Repository structure

~~~text
DevLoop/
├── backend/
│   ├── agents/
│   │   ├── file_identification.py
│   │   ├── hypothesis.py
│   │   ├── llm_utils.py
│   │   ├── log_analysis.py
│   │   ├── patch.py
│   │   └── report.py
│   ├── models/
│   ├── routers/
│   ├── tools/
│   ├── config.py
│   ├── database.py
│   ├── llm_client.py
│   ├── main.py
│   └── workflow.py
├── frontend/
│   ├── src/
│   ├── index.html
│   └── package.json
├── tests/
│   ├── test_api.py
│   ├── test_file_identification.py
│   ├── test_hypothesis.py
│   ├── test_log_analysis.py
│   ├── test_patch.py
│   ├── test_report.py
│   ├── test_tools.py
│   └── test_workflow.py
├── .dockerignore
├── .gitignore
├── Dockerfile
├── DEPLOY.md
└── render.yaml
~~~

---

## Demo story

A representative DevLoop run looks like this:

~~~text
1. Submit a repository and bug description
2. DevLoop clones the repository into an isolated workspace
3. Agents inspect failure evidence, files, and Git history
4. A root-cause hypothesis is generated
5. A patch candidate is generated
6. The candidate is validated against the repository and tests
7. The user reviews the patch
8. The user approves or rejects it
9. Approved patches are applied in the isolated workspace
10. Tests run again
11. DevLoop produces a resolution report
~~~

For the project's real-repository verification, the workflow successfully repaired the intentional coupon-calculation defect in pricing.py and finished with **2 passing tests** after approval.

---

## Design principles

**Evidence before assumptions**  
Repository contents and test expectations take precedence over unsupported model claims.

**Smallest safe change**  
Patch generation is instructed to make the minimum change that satisfies the observed failure.

**Validation before execution**  
A candidate patch must survive repository and test validation before approval.

**Human control**  
The system proposes a repair; the user decides whether the patch should be applied.

**Isolation by default**  
The debugging workflow operates on an isolated workspace rather than directly changing the source repository.

---

## Hackathon submission evidence

For IBM Bob hackathon submission requirements, task-session evidence can be placed in the repository under:

~~~text
bob_sessions/
~~~

Only include genuine task-session evidence that corresponds to the actual project work performed by each participant.

---

## Status

DevLoop currently includes:

- multi-agent debugging orchestration,
- local Ollama + Qwen support,
- Git URL and local-repository workflows,
- repository-aware evidence validation,
- Git history and diff inspection,
- structured patch generation,
- disposable pre-approval testing,
- human approval and LangGraph resume,
- retry-based repair flow,
- resolution reports,
- React dashboard,
- Docker/Render deployment configuration,
- public-mode authentication,
- automated test coverage.

The implementation has been exercised against a real Git repository and verified through the full debugging lifecycle.

---

## License

No separate open-source license is currently declared in this repository. Unless a license is added, the repository remains subject to the default copyright rules applicable to its owner.
