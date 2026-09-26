"""Root conftest: make the repo-root pytest invocation work.

The backend/ directory uses bare imports (e.g. ``from database import Base``,
``from agents.llm_utils import ...``) because it is meant to be launched via
``cd backend; uvicorn main:app``.  The test suite imports the same modules via
the ``backend.*`` package path (e.g. ``from backend.database import Base``).

We resolve this by:
1. Inserting backend/ at the front of sys.path so bare imports resolve.
2. Creating a lightweight namespace package for ``backend`` in sys.modules.
3. Eagerly importing every module under backend/ via their bare names and
   registering each one under both the bare name AND the ``backend.*`` path,
   so Python never loads the source file twice.
4. Setting submodule attributes on their parent namespace so monkeypatch
   string-form setattr (``"backend.agents.log_analysis.func"``) can navigate
   the dotted path with getattr().
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent / "backend"
_backend_str = str(_BACKEND_DIR)
if _backend_str not in sys.path:
    sys.path.insert(0, _backend_str)

# ---------------------------------------------------------------------------
# Create lightweight namespace modules for "backend" and "backend.agents"
# etc. so that ``import backend`` doesn't cause Python to look for a real
# package on disk (there's no backend/__init__.py).
# ---------------------------------------------------------------------------
_NAMESPACES = [
    "backend",
    "backend.agents",
    "backend.models",
    "backend.routers",
    "backend.tools",
]
for _ns in _NAMESPACES:
    if _ns not in sys.modules:
        _ns_mod = types.ModuleType(_ns)
        _ns_mod.__path__ = []  # type: ignore[attr-defined]
        _ns_mod.__package__ = _ns
        sys.modules[_ns] = _ns_mod

# Attach sub-namespaces as attributes on their parents.
sys.modules["backend"].agents  = sys.modules["backend.agents"]   # type: ignore[attr-defined]
sys.modules["backend"].models  = sys.modules["backend.models"]   # type: ignore[attr-defined]
sys.modules["backend"].routers = sys.modules["backend.routers"]  # type: ignore[attr-defined]
sys.modules["backend"].tools   = sys.modules["backend.tools"]    # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Bare-name â†’ backend.* alias pairs.  Import order: dependencies first.
# ---------------------------------------------------------------------------
_ALIASES: list[tuple[str, str]] = [
    ("config",                     "backend.config"),
    ("database",                   "backend.database"),
    ("llm_client",                 "backend.llm_client"),
    ("agents.llm_utils",           "backend.agents.llm_utils"),
    ("agents.log_analysis",        "backend.agents.log_analysis"),
    ("agents.file_identification", "backend.agents.file_identification"),
    ("agents.hypothesis",          "backend.agents.hypothesis"),
    ("agents.patch",               "backend.agents.patch"),
    ("agents.report",              "backend.agents.report"),
    ("agents",                     "backend.agents"),         # overwrite namespace with real pkg
    ("models.bug_report",          "backend.models.bug_report"),
    ("models.workflow_run",        "backend.models.workflow_run"),
    ("models.agent_step",          "backend.models.agent_step"),
    ("models.resolution_report",   "backend.models.resolution_report"),
    ("models",                     "backend.models"),
    ("tools.repo_tools",           "backend.tools.repo_tools"),
    ("tools.git_tools",            "backend.tools.git_tools"),
    ("tools.test_tools",           "backend.tools.test_tools"),
    ("tools",                      "backend.tools"),
    ("workflow",                   "backend.workflow"),
    ("routers.runs",               "backend.routers.runs"),
    ("routers.reports",            "backend.routers.reports"),
    ("routers",                    "backend.routers"),
    ("main",                       "backend.main"),
]

for _bare, _pkg in _ALIASES:
    # Import via bare name if not already loaded.
    if _bare not in sys.modules:
        try:
            sys.modules[_bare] = importlib.import_module(_bare)
        except Exception:
            continue
    # Register the same object under the backend.* path.
    sys.modules[_pkg] = sys.modules[_bare]

# ---------------------------------------------------------------------------
# Set submodule attributes on ALL parent package objects so pytest's
# monkeypatch can resolve dotted paths like
# "backend.agents.log_analysis.analyze_log" via successive getattr() calls.
# We do a second pass over sys.modules now that everything is registered.
# ---------------------------------------------------------------------------
_PARENT_CHILD: list[tuple[str, str, str]] = [
    # (parent_key, child_attr, child_key)
    ("backend",               "config",             "backend.config"),
    ("backend",               "database",           "backend.database"),
    ("backend",               "llm_client",         "backend.llm_client"),
    ("backend",               "workflow",           "backend.workflow"),
    ("backend",               "main",               "backend.main"),
    ("backend.agents",        "llm_utils",          "backend.agents.llm_utils"),
    ("backend.agents",        "log_analysis",       "backend.agents.log_analysis"),
    ("backend.agents",        "file_identification","backend.agents.file_identification"),
    ("backend.agents",        "hypothesis",         "backend.agents.hypothesis"),
    ("backend.agents",        "patch",              "backend.agents.patch"),
    ("backend.agents",        "report",             "backend.agents.report"),
    ("backend.models",        "bug_report",         "backend.models.bug_report"),
    ("backend.models",        "workflow_run",       "backend.models.workflow_run"),
    ("backend.models",        "agent_step",         "backend.models.agent_step"),
    ("backend.models",        "resolution_report",  "backend.models.resolution_report"),
    ("backend.tools",         "repo_tools",         "backend.tools.repo_tools"),
    ("backend.tools",         "git_tools",          "backend.tools.git_tools"),
    ("backend.tools",         "test_tools",         "backend.tools.test_tools"),
    ("backend.routers",       "runs",               "backend.routers.runs"),
    ("backend.routers",       "reports",            "backend.routers.reports"),
]
for _parent_key, _child_attr, _child_key in _PARENT_CHILD:
    _parent_mod = sys.modules.get(_parent_key)
    _child_mod = sys.modules.get(_child_key)
    if _parent_mod is not None and _child_mod is not None:
        try:
            setattr(_parent_mod, _child_attr, _child_mod)
        except (AttributeError, TypeError):
            pass

# Re-bind the real sub-packages on the backend namespace after aliases have
# replaced the placeholder namespace objects with the real package objects.
for _sub in ("agents", "models", "tools", "routers"):
    _real = sys.modules.get(f"backend.{_sub}")
    if _real is not None:
        try:
            setattr(sys.modules["backend"], _sub, _real)
        except (AttributeError, TypeError):
            pass
