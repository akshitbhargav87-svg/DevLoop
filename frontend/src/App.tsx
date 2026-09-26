import { useLayoutEffect, useState } from "react";
import "./App.css";

type Theme = "dark" | "light";

function getInitialTheme(): Theme {
  const savedTheme = window.localStorage.getItem("devloop-theme");
  if (savedTheme === "dark" || savedTheme === "light") {
    return savedTheme;
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

type StepStatus = "done" | "running" | "pending";

type AgentStep = {
  name: string;
  description: string;
  status: StepStatus;
  time?: string;
};

const initialSteps: AgentStep[] = [
  {
    name: "Repository Clone",
    description: "Preparing isolated workspace",
    status: "done",
    time: "12s",
  },
  {
    name: "Log Analysis",
    description: "Inspecting failure output",
    status: "done",
    time: "8s",
  },
  {
    name: "File Identification",
    description: "Finding affected modules",
    status: "done",
    time: "5s",
  },
  {
    name: "Hypothesis",
    description: "Synthesizing root-cause evidence",
    status: "running",
  },
  {
    name: "Patch Generation",
    description: "Waiting for hypothesis",
    status: "pending",
  },
  {
    name: "Test & Verify",
    description: "Waiting for patch",
    status: "pending",
  },
  {
    name: "Resolution Report",
    description: "Waiting for verification",
    status: "pending",
  },
];

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === "done") {
    return <span className="step-icon done">?</span>;
  }

  if (status === "running") {
    return <span className="step-icon running"><span /></span>;
  }

  return <span className="step-icon pending" />;
}

function App() {
  const [activePage, setActivePage] = useState("Overview");
  const [steps, setSteps] = useState(initialSteps);
  const [running, setRunning] = useState(true);
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("devloop-theme", theme);
  }, [theme]);

  const startRun = () => {
    setRunning(true);
    setSteps(initialSteps);
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
              <strong>Ollama � Qwen</strong>
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
              <h2>Checkout payment failure</h2>
              <p className="muted-text">
                DevLoop is investigating a failing test in <code>demo-target</code>.
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
              <strong className="green">{running ? "Investigating" : "Idle"}</strong>
              <small>Iteration 1 of 3</small>
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
                <span className="live-badge">
                  <span />
                  LIVE
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
                        {step.time && <span>{step.time}</span>}
                      </div>
                      <p>{step.description}</p>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel evidence-panel">
              <div className="panel-header">
                <div>
                  <span className="panel-kicker">ROOT-CAUSE EVIDENCE</span>
                  <h3>What DevLoop found</h3>
                </div>
                <span className="confidence">82% confidence</span>
              </div>

              <div className="hypothesis">
                <div className="hypothesis-icon">!</div>
                <div>
                  <span>Current hypothesis</span>
                  <strong>Coupon rate is being applied as the final payment multiplier.</strong>
                </div>
              </div>

              <div className="evidence-block">
                <span>AFFECTED FILE</span>
                <code>store/pricing.py</code>
              </div>

              <div className="evidence-block">
                <span>FAILING ASSERTION</span>
                <code>Expected 75.0, got 25.0</code>
              </div>

              <div className="evidence-block">
                <span>RECENT CHANGE</span>
                <code>calculate_discount()</code>
              </div>

              <button className="secondary-button">
                View evidence ?
              </button>
            </section>
          </div>

          <section className="panel bottom-panel">
            <div className="panel-header">
              <div>
                <span className="panel-kicker">WORKFLOW OUTPUT</span>
                <h3>Patch & verification</h3>
              </div>
              <span className="waiting">Waiting for hypothesis</span>
            </div>

            <div className="output-grid">
              <div className="output-card">
                <span>PATCH</span>
                <div className="placeholder-line large" />
                <div className="placeholder-line" />
                <div className="placeholder-line short" />
              </div>

              <div className="output-card">
                <span>TEST RESULTS</span>
                <div className="test-placeholder">
                  <span className="test-dot" />
                  Awaiting patch application
                </div>
              </div>

              <div className="output-card">
                <span>RESOLUTION REPORT</span>
                <div className="report-placeholder">
                  The final report will appear here after verification.
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

export default App;
