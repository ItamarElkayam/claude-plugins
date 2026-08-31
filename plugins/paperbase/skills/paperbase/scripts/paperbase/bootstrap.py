"""First-run environment setup: check the host, install what is genuinely required.

Policy (see references/ingestion-workflow.md#first-run-setup):

* Nothing is installed unless it is actually needed for the corpus at hand - a
  directory without PDFs never triggers an install.
* Installs go into a **private directory** (``~/.cache/paperbase/pylibs``, override
  with ``PAPERBASE_HOME``) using ``pip --target``. System and user site-packages are
  never touched, no sudo is used, externally-managed Pythons are unaffected, and the
  whole thing is removable with one ``rm -rf``.
* Every install is announced, recorded in the KB audit log, and opt-out-able with
  ``--no-install`` or ``PAPERBASE_NO_INSTALL=1``.
* Failure is never silent: the missing capability is reported with the exact command
  to run by hand, and the pipeline continues with reduced capability where it can.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import log

# Only dependencies that unlock a capability the corpus needs. Pinned to a floor
# version, upper-bounded to avoid a future breaking release landing silently.
REQUIREMENTS = {
    "pdf": [
        {"module": "fitz", "spec": "pymupdf>=1.23,<2", "why": "layout-aware PDF parsing "
         "(sections, columns, tables, page labels)"},
        {"module": "pypdf", "spec": "pypdf>=4,<7", "why": "fallback PDF text extraction"},
    ],
}
OPTIONAL_TOOLS = {
    "pdftoppm": "render PDF pages for the OCR fallback (poppler-utils)",
    "tesseract": "OCR engine for image-only PDFs",
}


def home() -> str:
    return os.environ.get("PAPERBASE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "paperbase")


def libdir() -> str:
    return os.path.join(home(), "pylibs")


def activate() -> str:
    """Put the private library directory on sys.path. Safe to call repeatedly."""
    path = libdir()
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
    return path


def _importable(module: str) -> bool:
    activate()
    try:
        __import__(module)
        return True
    except Exception:
        return False


def installs_allowed(no_install: bool = False) -> bool:
    if no_install:
        return False
    return os.environ.get("PAPERBASE_NO_INSTALL", "").strip().lower() not in ("1", "true", "yes")


def _pip_install(spec: str) -> Tuple[bool, str]:
    target = libdir()
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        return False, "cannot create %s: %s" % (target, exc)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
           "--no-input", "--target", target, "--upgrade", spec]
    log.info("installing %s into %s (private to paperbase; no system packages touched)",
             spec, target)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=600)
    except FileNotFoundError:
        return False, "python -m pip is unavailable (try: %s -m ensurepip --upgrade)" % sys.executable
    except subprocess.TimeoutExpired:
        return False, "pip timed out after 600s (offline or a very slow mirror?)"
    if proc.returncode != 0:
        tail = (proc.stdout or b"").decode("utf-8", "replace").strip().split("\n")[-4:]
        return False, "pip failed (exit %d): %s" % (proc.returncode, " | ".join(tail))
    activate()
    return True, "installed %s" % spec


def ensure(capability: str, no_install: bool = False) -> Dict[str, Any]:
    """Ensure one capability. Returns a result dict; never raises."""
    result: Dict[str, Any] = {"capability": capability, "satisfied_by": None,
                              "installed": [], "attempts": [], "actions": []}
    needs = REQUIREMENTS.get(capability, [])
    for req in needs:
        if _importable(req["module"]):
            result["satisfied_by"] = req["module"]
            return result
    if not needs:
        return result
    if not installs_allowed(no_install):
        result["actions"].append(
            "automatic installation is disabled (--no-install / PAPERBASE_NO_INSTALL). "
            "To enable %s support run: %s -m pip install --target %s '%s'"
            % (capability, sys.executable, libdir(), needs[0]["spec"]))
        return result
    for req in needs:
        ok, detail = _pip_install(req["spec"])
        result["attempts"].append({"spec": req["spec"], "ok": ok, "detail": detail})
        if ok and _importable(req["module"]):
            result["satisfied_by"] = req["module"]
            result["installed"].append(req["spec"])
            log.info("%s support ready via %s", capability, req["module"])
            return result
        log.debug("install attempt for %s failed: %s", req["spec"], detail)
    result["actions"].append(
        "could not obtain %s support automatically. Install it by hand with:\n"
        "    %s -m pip install '%s'\n"
        "or into paperbase's private directory:\n"
        "    %s -m pip install --target %s '%s'"
        % (capability, sys.executable, needs[0]["spec"], sys.executable, libdir(),
           needs[0]["spec"]))
    return result


def which(tool: str) -> Optional[str]:
    for directory in (os.environ.get("PATH") or "").split(os.pathsep):
        candidate = os.path.join(directory, tool)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def doctor(needed_capabilities: Optional[List[str]] = None, no_install: bool = False) -> Dict[str, Any]:
    """Full environment report; installs the capabilities that are actually needed."""
    activate()
    report: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 9),
        "executable": sys.executable,
        "paperbase_home": home(),
        "private_libdir": libdir(),
        "private_libdir_exists": os.path.isdir(libdir()),
        "installs_allowed": installs_allowed(no_install),
        "capabilities": {},
        "optional_tools": {},
        "sqlite": {},
        "actions": [],
    }
    if not report["python_ok"]:
        report["actions"].append(
            "Python %s is too old; paperbase needs 3.9+." % report["python"])

    import sqlite3
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        report["sqlite"] = {"version": sqlite3.sqlite_version, "fts5": True}
    except Exception as exc:
        report["sqlite"] = {"version": sqlite3.sqlite_version, "fts5": False, "error": str(exc)}
        report["actions"].append(
            "this Python's sqlite3 lacks FTS5, so `kb search`/`kb context` cannot run. "
            "Use a Python built with FTS5 (e.g. python.org or homebrew builds).")
    finally:
        conn.close()

    for capability in (needed_capabilities if needed_capabilities is not None else list(REQUIREMENTS)):
        outcome = ensure(capability, no_install=no_install)
        report["capabilities"][capability] = outcome
        report["actions"].extend(outcome["actions"])

    for tool, why in OPTIONAL_TOOLS.items():
        path = which(tool)
        report["optional_tools"][tool] = {"path": path, "why": why}
    if not all(v["path"] for v in report["optional_tools"].values()):
        report["actions"].append(
            "OCR is unavailable (missing %s). Image-only PDFs will be flagged rather than "
            "recovered - that is by design; install poppler-utils and tesseract if you need it."
            % ", ".join(t for t, v in report["optional_tools"].items() if not v["path"]))
    return report


def ensure_for_corpus(extensions: List[str], no_install: bool = False) -> Dict[str, Any]:
    """Install only what this corpus actually needs (PDFs present -> PDF support)."""
    needed = []
    if any(ext.lower() == ".pdf" for ext in extensions):
        needed.append("pdf")
    if not needed:
        return {"needed": [], "capabilities": {}}
    out = {"needed": needed, "capabilities": {}}
    for capability in needed:
        out["capabilities"][capability] = ensure(capability, no_install=no_install)
    return out


def format_report(report: Dict[str, Any]) -> str:
    lines = [
        "paperbase environment",
        "  python            %s (%s)%s" % (report["python"], report["executable"],
                                           "" if report["python_ok"] else "  TOO OLD"),
        "  sqlite            %s, FTS5 %s" % (report["sqlite"].get("version"),
                                             "yes" if report["sqlite"].get("fts5") else "NO"),
        "  private libdir    %s%s" % (report["private_libdir"],
                                      "" if report["private_libdir_exists"] else " (not created yet)"),
        "  auto-install      %s" % ("enabled" if report["installs_allowed"] else "disabled"),
    ]
    for capability, outcome in sorted(report["capabilities"].items()):
        state = ("via %s" % outcome["satisfied_by"]) if outcome["satisfied_by"] else "UNAVAILABLE"
        extra = (" (installed %s)" % ", ".join(outcome["installed"])) if outcome["installed"] else ""
        lines.append("  %-17s %s%s" % (capability, state, extra))
    for tool, info in sorted(report["optional_tools"].items()):
        lines.append("  %-17s %s" % (tool, info["path"] or "not found - %s" % info["why"]))
    if report["actions"]:
        lines.append("")
        lines.append("Actions:")
        for action in report["actions"]:
            lines.append("  - %s" % action.replace("\n", "\n    "))
    return "\n".join(lines)
