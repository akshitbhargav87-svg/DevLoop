import { useEffect, useLayoutEffect, useState, type FormEvent } from "react";
import "./App.css";

type Theme = "dark" | "light";
type RepositorySource = "local" | "git";

function getInitialTheme(): Theme {
  const savedTheme = window.localStorage.getItem("devloop-theme");
  if (savedTheme === "dark" || savedTheme === "light") {
    return savedTheme;
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

type StepStatus = "done" | "running" | "pending" | "failed";

type AgentStep = {
  key: string;
  name: string;
  description: string;
  status: StepStatus;
};

type RunSnapshot = {
  status: string;
  iteration: number;
  steps: Array<{
    step_name: string;
    status: string;
    evidence?: unknown;
  }>;
};

const workflowStages: Omit<AgentStep, "status">[] = [
  {
    key: "clone_repo",
    name: "Repository Clone",
    description: "Preparing isolated workspace",
  },
  {
    key: "log_analysis",
    name: "Log Analysis",
    description: "Inspecting failure output",
  },
  {
    key: "file_identification",
    name: "File Identification",
    description: "Finding affected modules",
  },
  {
    key: "change_inspection",
    name: "Change Inspection",
    description: "Reviewing recent repository changes",
  },
  {
    key: "hypothesis",
    name: "Hypothesis",
    description: "Synthesizing root-cause evidence",
  },
  {
    key: "patch",
    name: "Patch Generation",
    description: "Waiting for hypothesis",
  },
  {
    key: "awaiting_approval",
    name: "Patch Review",
    description: "Waiting for patch review",
  },
  {
    key: "apply_patch",
    name: "Apply Patch",
    description: "Waiting for approval",
  },
  {
    key: "test_runner",
    name: "Test & Verify",
    description: "Waiting for patch",
  },
  {
    key: "report",
    name: "Resolution Report",
    description: "Waiting for verification",
  },
];

const initialSteps: AgentStep[] = workflowStages.map((stage) => ({
  ...stage,
  status: "pending",
}));

function toStepStatus(status: string): StepStatus {
  if (status === "completed") {
    return "done";
  }
  if (status === "running") {
    return "running";
  }
  if (status === "failed") {
    return "failed";
  }
  return "pending";
}

function mapRunSteps(records: RunSnapshot["steps"]): AgentStep[] {
  const latestByName = new Map(records.map((record) => [record.step_name, record]));

  return workflowStages.map((stage) => {
    const record = latestByName.get(stage.key);
    return {
      ...stage,
      status: record ? toStepStatus(record.status) : "pending",
    };
  });
}

function runStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    idle: "Idle",
    pending: "Starting",
    running: "Investigating",
    awaiting_approval: "Awaiting approval",
    approved: "Approved",
    rejected: "Rejected",
    completed: "Completed",
    failed: "Failed",
  };
  return labels[status] ?? status;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function parseJsonRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "string") {
    return asRecord(value);
  }

  try {
    return asRecord(JSON.parse(value));
  } catch {
    return {};
  }
}

function eventStepStatus(eventType: string): StepStatus | undefined {
  if (eventType === "step_started") {
    return "running";
  }
  if (eventType === "step_completed") {
    return "done";
  }
  if (eventType === "step_failed") {
    return "failed";
  }
  return undefined;
}

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === "done") {
    return <span className="step-icon done">âœ“</span>;
  }

  if (status === "failed") {
    return <span className="step-icon failed">!</span>;
  }

  if (status === "running") {
    return <span className="step-icon running"><span /></span>;
  }

  return <span className="step-icon pending" />;
}

function App() {
  const [activePage, setActivePage] = useState("Overview");
  const [steps, setSteps] = useState(initialSteps);
  const [stepEvidence, setStepEvidence] = useState<Record<string, Record<string, unknown>>>({});
  const [runStatus, setRunStatus] = useState("idle");
  const [runError, setRunError] = useState("");
  const [approvalError, setApprovalError] = useState("");
  const [isApproving, setIsApproving] = useState(false);
  const [activeRunId, setActiveRunId] = useState(
    () => new URLSearchParams(window.location.search).get("run_id") ?? "",
  );
  const [iteration, setIteration] = useState(1);
  const [runTitle, setRunTitle] = useState("");
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [showNewRun, setShowNewRun] = useState(false);
  const [repositorySource, setRepositorySource] = useState<RepositorySource>("local");
  const [repositoryValue, setRepositoryValue] = useState("");
  const [branch, setBranch] = useState("main");
  const [testCommand, setTestCommand] = useState("pytest -q");
  const [bugTitle, setBugTitle] = useState("");
  const [bugDescription, setBugDescription] = useState("");
  const [stackTrace, setStackTrace] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("devloop-theme", theme);
  }, [theme]);

  useEffect(() => {
    if (!activeRunId) {
      return;
    }

    let disposed = false;
    let terminal = false;
    const refreshRun = async () => {
      try {
        const response = await fetch(`/api/runs/${activeRunId}`);
        if (!response.ok) {
          throw new Error(`Could not load run status (${response.status})`);
        }
        const snapshot = await response.json() as RunSnapshot;
        if (disposed) {
          return;
        }
        setSteps(mapRunSteps(snapshot.steps));
        setStepEvidence(Object.fromEntries(
          snapshot.steps.map((step) => [step.step_name, asRecord(step.evidence)]),
        ));
        setRunStatus(snapshot.status);
        setIteration(snapshot.iteration);
        setRunError("");
        terminal = ["completed", "rejected", "failed"].includes(snapshot.status);
        if (snapshot.status === "failed") {
          setRunError("The workflow failed. See the failed step for details.");
          const failedStep = [...snapshot.steps].reverse().find((step) => step.status === "failed");
          const evidence = failedStep?.evidence;
          if (evidence && typeof evidence === "object" && "error" in evidence) {
            setRunError(String(evidence.error));
          }
        }
      } catch (error) {
        if (!disposed) {
          setRunError(error instanceof Error ? error.message : "Could not load run status.");
        }
      }
    };

    const eventSource = new EventSource(`/api/runs/${activeRunId}/events`);
    const handleEvent = (event: Event) => {
      const message = event as MessageEvent<string>;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(message.data) as Record<string, unknown>;
      } catch {
        return;
      }

      const status = eventStepStatus(payload.type as string);
      if (status && typeof payload.step_name === "string") {
        setSteps((current) => current.map((step) => (
          step.key === payload.step_name ? { ...step, status } : step
        )));
        setRunStatus(status === "failed" ? "failed" : "running");
      }
      if (payload.type === "step_completed" && typeof payload.step_name === "string") {
        setStepEvidence((current) => ({
          ...current,
          [payload.step_name as string]: asRecord(payload.evidence),
        }));
      }
      if (payload.type === "step_failed" || payload.type === "run_failed") {
        terminal = true;
        setRunStatus("failed");
        setRunError(String(payload.error ?? "The workflow failed."));
      }
      if (payload.type === "run_completed") {
        terminal = true;
        void refreshRun();
      }
    };

    ["step_started", "step_completed", "step_failed", "run_failed", "run_completed"]
      .forEach((eventName) => eventSource.addEventListener(eventName, handleEvent));
    void refreshRun();
    const pollId = window.setInterval(() => {
      if (!terminal) {
        void refreshRun();
      }
    }, 2000);

    return () => {
      disposed = true;
      window.clearInterval(pollId);
      eventSource.close();
    };
  }, [activeRunId]);

  const startRun = () => {
    setSubmitError("");
    setShowNewRun(true);
  };

  const decideApproval = async (approved: boolean) => {
    if (!activeRunId) {
      return;
    }

    setIsApproving(true);
    setApprovalError("");
    try {
      const response = await fetch(`/api/runs/${activeRunId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      });
      const result = await response.json() as { detail?: string; status?: string };
      if (!response.ok) {
        throw new Error(result.detail || `Could not submit approval (${response.status})`);
      }
      if (result.status) {
        setRunStatus(result.status);
      }
    } catch (error) {
      setApprovalError(error instanceof Error ? error.message : "Could not submit approval.");
    } finally {
      setIsApproving(false);
    }
  };

  const hypothesis = asRecord(stepEvidence.hypothesis);
  const logAnalysis = asRecord(stepEvidence.log_analysis);
  const identifiedFiles = Array.isArray(asRecord(stepEvidence.file_identification).files)
    ? asRecord(stepEvidence.file_identification).files as unknown[]
    : [];
  const hypothesisFiles = Array.isArray(hypothesis.affected_files)
    ? hypothesis.affected_files.filter((path): path is string => typeof path === "string")
    : [];
  const identifiedPath = identifiedFiles
    .map((item) => asRecord(item).path)
    .find((path): path is string => typeof path === "string");
  const identifiedReason = typeof asRecord(identifiedFiles[0]).relevance_reason === "string"
    ? asRecord(identifiedFiles[0]).relevance_reason as string
    : "";
  const affectedFile = hypothesisFiles[0] ?? identifiedPath ?? "No file identified yet";
  const failureEvidence = [
    typeof logAnalysis.exception === "string" && logAnalysis.exception !== "Unknown"
      ? logAnalysis.exception
      : undefined,
    typeof logAnalysis.module === "string" && logAnalysis.module
      ? `at ${logAnalysis.module}${typeof logAnalysis.line === "number" && logAnalysis.line > 0 ? `:${logAnalysis.line}` : ""}`
      : undefined,
  ].filter((part): part is string => Boolean(part)).join(" ");
  const callChain = Array.isArray(logAnalysis.call_chain)
    ? logAnalysis.call_chain.filter((item): item is string => typeof item === "string")
    : [];
  const recentCommits = Array.isArray(asRecord(stepEvidence.change_inspection).commits)
    ? asRecord(stepEvidence.change_inspection).commits as unknown[]
    : [];
  const recentChange = typeof asRecord(recentCommits[0]).message === "string"
    ? asRecord(recentCommits[0]).message as string
    : "No recent change evidence";
  const patchEvidence = asRecord(stepEvidence.patch);
  const patchValidation = asRecord(patchEvidence.preapproval_validation);
  const patchValidationTests = asRecord(patchValidation.test_results);
  const patchApplication = asRecord(stepEvidence.apply_patch);
  const testEvidence = asRecord(stepEvidence.test_runner);
  const reportEvidence = asRecord(stepEvidence.report);
  const reportDetails = parseJsonRecord(reportEvidence.evidence);
  const testErrors = Array.isArray(testEvidence.errors)
    ? testEvidence.errors.filter((error): error is string => typeof error === "string")
    : [];
  const changedFiles = Array.isArray(patchEvidence.files_changed)
    ? patchEvidence.files_changed.filter((path): path is string => typeof path === "string")
    : [];
  const patchText = typeof patchEvidence.patch === "string" ? patchEvidence.patch : "";
  const testOutput = typeof testEvidence.output_excerpt === "string"
    ? testEvidence.output_excerpt
    : "";
  const patchStatus = !Object.keys(patchEvidence).length
    ? "Waiting for patch"
    : patchApplication.success === false
      ? "Patch application failed"
      : patchApplication.success === true
        ? "Patch applied"
        : patchValidation.passed === true
          ? "Pre-approval tests passed"
        : "Patch generated";
  const testStatus = !Object.keys(testEvidence).length
    ? "Tests have not run"
    : testErrors.length > 0
      ? "Test run blocked"
      : Number(testEvidence.failed) > 0
        ? "Tests failed"
        : Number(testEvidence.passed) > 0
          ? "Tests passed"
          : "No tests reported";
  const verificationBlocked = testErrors.length > 0;
  const displayRunStatus = runStatus === "completed" && verificationBlocked
    ? "Verification blocked"
    : runStatusLabel(runStatus);
  const reportText = typeof reportEvidence.root_cause === "string"
    ? reportEvidence.root_cause
    : typeof reportDetails.root_cause === "string"
      ? reportDetails.root_cause
      : "No resolution report has been generated yet.";

  const createRun = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setSubmitError("");

    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: bugTitle,
          description: bugDescription,
          stack_trace: stackTrace || null,
          repo_url: repositoryValue,
          repository_type: repositorySource,
          branch,
          test_command: testCommand,
        }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || `Could not start the run (${response.status})`);
      }
      setRunTitle(bugTitle);
      setSteps(initialSteps);
      setStepEvidence({});
      setRunStatus("pending");
      setRunError("");
      setIteration(1);
      setActiveRunId(result.run_id);
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set("run_id", result.run_id);
      window.history.replaceState(null, "", nextUrl);
      setShowNewRun(false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Could not start the run.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">D</div>
          <div>
            <strong>DevLoop</strong>
            <span>AI Debugging</span>
          </div>
        </div>

        <nav className="nav">
          <p className="nav-label">WORKSPACE</p>

          {["Overview", "Debug Runs", "Reports"].map((item) => (
            <button
              key={item}
              className={`nav-item ${activePage === item ? "active" : ""}`}
              onClick={() => setActivePage(item)}
            >
              <span className="nav-dot" />
              {item}
            </button>
          ))}

          <p className="nav-label second">SYSTEM</p>

          {["Repository", "Settings"].map((item) => (
            <button
              key={item}
              className={`nav-item ${activePage === item ? "active" : ""}`}
              onClick={() => setActivePage(item)}
            >
              <span className="nav-dot muted" />
              {item}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="model-card">
            <div className="online-dot" />
            <div>
              <span>Local AI</span>
              <strong>Ollama ï¿½ Qwen</strong>
            </div>
            <span className="status-label">ONLINE</span>
          </div>

          <div className="version">DevLoop v0.1.0</div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">DEVELOPMENT WORKSPACE</p>
            <h1>{activePage}</h1>
          </div>

          <div className="topbar-actions">
            <div className="connection">
              <span className="online-dot" />
              Backend connected
            </div>

            <button
              className="theme-toggle"
              type="button"
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              aria-pressed={theme === "dark"}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? (
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
                </svg>
              ) : (
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path d="M20.2 15.1A8.5 8.5 0 0 1 8.9 3.8 8.6 8.6 0 1 0 20.2 15.1Z" />
                </svg>
              )}
            </button>

            <button className="avatar">A</button>
          </div>
        </header>

        <div className="content">
          <section className="hero-row">
            <div>
              <p className="eyebrow">CURRENT DEBUG SESSION</p>
              <h2>{runTitle || "Debug workflow"}</h2>
              <p className="muted-text">
                {activeRunId
                  ? <>Run <code>{activeRunId}</code> is connected to live workflow updates.</>
                  : "Start a debug run to see its agent progress here."}
              </p>
            </div>

            <button className="primary-button" onClick={startRun}>
              <span>+</span>
              New Debug Run
            </button>
          </section>

          <section className="stats-grid">
            <div className="stat-card">
              <span>RUN STATUS</span>
              <strong className={runStatus === "failed" || verificationBlocked ? "run-failed" : "green"}>
                {displayRunStatus}
              </strong>
              <small>Iteration {iteration} of 3</small>
            </div>

            <div className="stat-card">
              <span>AGENTS</span>
              <strong>7</strong>
              <small>Specialized agents</small>
            </div>

            <div className="stat-card">
              <span>MODEL</span>
              <strong>Qwen</strong>
              <small>Running locally via Ollama</small>
            </div>

            <div className="stat-card">
              <span>WORKSPACE</span>
              <strong>Isolated</strong>
              <small>demo-target repository</small>
            </div>
          </section>

          <div className="dashboard-grid">
            <section className="panel pipeline-panel">
              <div className="panel-header">
                <div>
                  <span className="panel-kicker">AGENT PIPELINE</span>
                  <h3>Debug workflow</h3>
                </div>
                <span className={`live-badge ${runStatus === "failed" ? "failed" : ""}`}>
                  <span />
                  {runStatus === "running" || runStatus === "pending"
                    ? "LIVE"
                    : runStatus === "awaiting_approval"
                      ? "REVIEW"
                      : runStatus === "idle"
                        ? "IDLE"
                        : displayRunStatus.toUpperCase()}
                </span>
              </div>

              <div className="timeline">
                {steps.map((step, index) => (
                  <div className="timeline-item" key={step.name}>
                    <div className="timeline-marker">
                      <StatusIcon status={step.status} />
                      {index < steps.length - 1 && <span className="connector" />}
                    </div>

                    <div className="timeline-content">
                      <div className="step-title">
                        <strong>{step.name}</strong>
                        {step.status === "running" && <span>Running</span>}
                        {step.status === "failed" && <span>Failed</span>}
                      </div>
                      <p>{step.description}</p>
                    </div>
                  </div>
                ))}
              </div>
              {runError && <p className="form-error" role="alert">{runError}</p>}
              {runStatus === "awaiting_approval" && (
                <div className="workflow-actions">
                  {approvalError && <p className="form-error" role="alert">{approvalError}</p>}
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={isApproving}
                    onClick={() => void decideApproval(false)}
                  >
                    Reject patch
                  </button>
                  <button
                    className="primary-button"
                    type="button"
                    disabled={isApproving}
                    onClick={() => void decideApproval(true)}
                  >
                    {isApproving ? "Submittingâ€¦" : "Approve & run tests"}
                  </button>
                </div>
              )}
            </section>

            <section className="panel evidence-panel">
              <div className="panel-header">
                <div>
                  <span className="panel-kicker">ROOT-CAUSE EVIDENCE</span>
                  <h3>What DevLoop found</h3>
                </div>
                <span className="confidence">
                  {typeof hypothesis.confidence === "number"
                    ? `${Math.round(hypothesis.confidence * 100)}% confidence`
                    : "No confidence yet"}
                </span>
              </div>

              <div className="hypothesis">
                <div className="hypothesis-icon">!</div>
                <div>
                  <span>Current hypothesis</span>
                  <strong>
                    {typeof hypothesis.root_cause === "string"
                      ? hypothesis.root_cause
                      : "No hypothesis is available yet."}
                  </strong>
                </div>
              </div>

              <div className="evidence-block">
                <span>CANDIDATE FILE</span>
                <code>{affectedFile}</code>
                {identifiedReason && <p className="output-detail">{identifiedReason}</p>}
              </div>

              <div className="evidence-block">
                <span>FAILURE LOCATION</span>
                <code>{failureEvidence || "No failure details available yet"}</code>
              </div>

              {callChain.length > 0 && (
                <div className="evidence-block">
                  <span>STACK TRACE CALL CHAIN</span>
                  <code>{callChain.join(" â†’ ")}</code>
                </div>
              )}

              <div className="evidence-block">
                <span>RECENT CHANGE</span>
                <code>{recentChange}</code>
              </div>
            </section>
          </div>

          <section className="panel bottom-panel">
            <div className="panel-header">
              <div>
                <span className="panel-kicker">WORKFLOW OUTPUT</span>
                <h3>Patch & verification</h3>
              </div>
              <span className={`waiting ${patchApplication.success === false || testErrors.length > 0 ? "output-failed" : ""}`}>
                {patchApplication.success === false || testErrors.length > 0
                  ? "Needs attention"
                  : runStatusLabel(runStatus)}
              </span>
            </div>

            <div className="output-grid">
              <div className="output-card">
                <span>PATCH</span>
                <strong className={patchApplication.success === false ? "output-error-text" : ""}>
                  {patchStatus}
                </strong>
                {typeof patchEvidence.explanation === "string" && (
                  <p className="output-detail">{patchEvidence.explanation}</p>
                )}
                {patchValidation.passed === true && (
                  <p className="output-detail">
                    Verified in an isolated copy: {Number(patchValidationTests.passed) || 0} tests passed.
                  </p>
                )}
                {changedFiles.length > 0 && (
                  <p className="output-detail">Files: {changedFiles.join(", ")}</p>
                )}
                {patchText && (
                  <pre className="patch-diff">{patchText}</pre>
                )}
                {!patchText && <p className="output-detail">No patch was produced.</p>}
              </div>

              <div className="output-card">
                <span>TEST RESULTS</span>
                <strong className={testErrors.length > 0 ? "output-error-text" : ""}>{testStatus}</strong>
                {Object.keys(testEvidence).length > 0 && (
                  <p className="output-detail">
                    {Number(testEvidence.passed) || 0} passed, {Number(testEvidence.failed) || 0} failed
                  </p>
                )}
                {testErrors.map((error, index) => (
                  <pre className="test-error" key={`${index}-${error}`}>{error}</pre>
                ))}
                {testOutput && <pre className="patch-diff">{testOutput}</pre>}
              </div>

              <div className="output-card">
                <span>RESOLUTION REPORT</span>
                <p className="output-detail">{reportText}</p>
                {typeof reportDetails.changes_made === "string" && reportDetails.changes_made && (
                  <pre className="patch-diff">{reportDetails.changes_made}</pre>
                )}
                {typeof reportDetails.test_results === "string" && (
                  <pre className="patch-diff">{reportDetails.test_results}</pre>
                )}
              </div>
            </div>
          </section>
        </div>

        {showNewRun && (
          <div
            className="modal-backdrop"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget && !isSubmitting) {
                setShowNewRun(false);
              }
            }}
          >
            <section
              className="new-run-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="new-run-title"
            >
              <div className="new-run-heading">
                <div>
                  <span className="panel-kicker">START AN INVESTIGATION</span>
                  <h2 id="new-run-title">New Debug Run</h2>
                  <p>DevLoop works on an isolated copy of your repository.</p>
                </div>
                <button
                  className="dialog-close"
                  type="button"
                  aria-label="Close new debug run"
                  disabled={isSubmitting}
                  onClick={() => setShowNewRun(false)}
                >
                  Ã—
                </button>
              </div>

              <form className="new-run-form" onSubmit={createRun}>
                  <fieldset className="source-fieldset">
                    <legend>Repository source</legend>
                    <div className="source-options">
                      <label className={repositorySource === "local" ? "selected" : ""}>
                        <input
                          type="radio"
                          name="repository-source"
                          value="local"
                          checked={repositorySource === "local"}
                          onChange={() => setRepositorySource("local")}
                        />
                        <span aria-hidden="true">ðŸ“</span>
                        Local Repository
                      </label>
                      <label className={repositorySource === "git" ? "selected" : ""}>
                        <input
                          type="radio"
                          name="repository-source"
                          value="git"
                          checked={repositorySource === "git"}
                          onChange={() => setRepositorySource("git")}
                        />
                        <span aria-hidden="true">â†—</span>
                        Git URL
                      </label>
                    </div>
                  </fieldset>

                  <label className="form-field">
                    {repositorySource === "local" ? "Repository folder path" : "Git repository URL"}
                    <input
                      required
                      value={repositoryValue}
                      onChange={(event) => setRepositoryValue(event.target.value)}
                      placeholder={repositorySource === "local" ? "C:\\Users\\you\\Projects\\my-app" : "https://github.com/user/project.git"}
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <small>
                      {repositorySource === "local"
                        ? "Use the full path to a local Git working tree."
                        : "Enter an HTTPS or SSH clone URL accessible to this machine."}
                    </small>
                  </label>

                  <div className="form-row">
                    <label className="form-field">
                      Branch
                      <input
                        required
                        value={branch}
                        onChange={(event) => setBranch(event.target.value)}
                        placeholder="main"
                        autoComplete="off"
                      />
                    </label>
                    <label className="form-field">
                      Test command
                      <input
                        required
                        value={testCommand}
                        onChange={(event) => setTestCommand(event.target.value)}
                        placeholder="pytest -q"
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                  </div>

                  <label className="form-field">
                    Issue title
                    <input
                      required
                      value={bugTitle}
                      onChange={(event) => setBugTitle(event.target.value)}
                      placeholder="Checkout total is incorrect"
                    />
                  </label>

                  <label className="form-field">
                    What is going wrong?
                    <textarea
                      required
                      rows={3}
                      value={bugDescription}
                      onChange={(event) => setBugDescription(event.target.value)}
                      placeholder="Describe the behavior you expected and what happened instead."
                    />
                  </label>

                  <label className="form-field">
                    Failure output <span className="optional-label">Optional</span>
                    <textarea
                      rows={3}
                      value={stackTrace}
                      onChange={(event) => setStackTrace(event.target.value)}
                      placeholder="Paste a stack trace or failing test output."
                      spellCheck={false}
                    />
                  </label>

                  {submitError && <p className="form-error" role="alert">{submitError}</p>}

                  <div className="form-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={isSubmitting}
                      onClick={() => setShowNewRun(false)}
                    >
                      Cancel
                    </button>
                    <button className="primary-button" type="submit" disabled={isSubmitting}>
                      {isSubmitting ? "Startingâ€¦" : "Start Debug Run"}
                    </button>
                  </div>
              </form>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
