"""
Custom tools available to the Architectural Reviewer agent node.

The reviewer is equipped with a code_execution tool so it can run linting
commands directly inside the workflow rather than relying solely on the LLM's
knowledge of what violations exist.

Note: google.adk.code_executors provides the built-in BuiltInCodeExecutor.
For local prototype use we also expose a lightweight shell-based executor
that the agent can call as a regular function tool.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def run_ruff_lint(code_snippet: str, filename: str = "snippet.py") -> dict:
    """Run ruff linter on a Python code snippet and return structured results.

    Args:
        code_snippet: Raw Python source code to lint.
        filename: Virtual filename to use for ruff output (affects rule selection).

    Returns:
        A dict with keys:
            - ``violations``: list of dicts with keys file, line, col, rule, message.
            - ``exit_code``: ruff exit code (0 = clean, 1 = violations, 2+ = error).
            - ``raw_output``: Full ruff stdout/stderr as a string.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / filename
        target.write_text(code_snippet, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--output-format=json", str(target)],
            capture_output=True,
            text=True,
        )

        violations: list[dict] = []
        try:
            import json

            raw = json.loads(result.stdout) if result.stdout.strip() else []
            for item in raw:
                violations.append(
                    {
                        "file": filename,
                        "line": item.get("location", {}).get("row"),
                        "col": item.get("location", {}).get("column"),
                        "rule": item.get("code", "UNKNOWN"),
                        "message": item.get("message", ""),
                        "severity": "error" if item.get("fix") is None else "warning",
                    }
                )
        except Exception:
            pass  # Return raw output if JSON parse fails

        return {
            "violations": violations,
            "exit_code": result.returncode,
            "raw_output": result.stdout + result.stderr,
        }


def run_shell_command(command: str, working_directory: str = ".") -> dict:
    """Execute an arbitrary shell command and return its output.

    Use this tool to run project-specific linting scripts, test runners,
    or baseline verification commands not covered by run_ruff_lint.

    Args:
        command: Shell command to execute (e.g. ``"ruff check app/"``).
        working_directory: Directory to run the command in.

    Returns:
        A dict with keys ``stdout``, ``stderr``, and ``exit_code``.

    Security note:
        This tool runs arbitrary commands. In production, restrict to an
        allowlist of safe commands or use a sandboxed executor instead.
    """
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=working_directory,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


def check_python_syntax(code_snippet: str) -> dict:
    """Check whether a Python snippet has valid syntax.

    Args:
        code_snippet: Raw Python source code.

    Returns:
        A dict with keys ``valid`` (bool), ``error`` (str | None), and ``line`` (int | None).
    """
    try:
        compile(code_snippet, "<snippet>", "exec")
        return {"valid": True, "error": None, "line": None}
    except SyntaxError as exc:
        return {"valid": False, "error": str(exc.msg), "line": exc.lineno}


# Expose a flat list for easy registration on LlmAgent(tools=...)
REVIEWER_TOOLS = [
    run_ruff_lint,
    run_shell_command,
    check_python_syntax,
]
