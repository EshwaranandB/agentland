import os
import shutil
import subprocess
import time
import re
from abc import ABC, abstractmethod
from pathlib import Path


class WorkerRunner(ABC):
    identity = "unknown"

    @abstractmethod
    def run(self, workspace):
        """Return public execution metadata only; never private reasoning."""


class DeterministicWorkerRunner(WorkerRunner):
    identity = "deterministic"

    def run(self, workspace):
        target = Path(workspace) / "src" / "rooms.js"
        source = target.read_text(encoding="utf-8")
        target.write_text(source.replace("return strokes;", "return strokes.filter((stroke) => stroke.roomId === roomId);"), encoding="utf-8")
        return {"exit_code": 0, "duration_ms": 0, "stdout": "Applied deterministic room-isolation repair.", "stderr": ""}


class CodexWorkerRunner(WorkerRunner):
    identity = "codex"

    def run(self, workspace):
        """Run only the documented non-interactive workspace-write mode."""
        executable = shutil.which("codex")
        if not executable:
            return {
                "exit_code": -1, "duration_ms": 0, "stdout": "", "stderr": "Codex CLI is unavailable",
                "cli_version": "unavailable", "codex_started": False,
                "error_category": "process_launch_unavailable", "platform_error": "",
            }
        root = Path(workspace).resolve()
        command = [
            executable, "exec", "--ephemeral", "--sandbox", "workspace-write",
            "Repair the deliberate cross-room stroke-isolation defect in src/rooms.js. "
            "Modify only implementation files required for this repair. Do not modify tests.",
        ]
        safe_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC") if key in os.environ}
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=str(root), shell=False, env=safe_env, text=True, capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as error:
            return {"exit_code": -1, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": _public_output(error.stdout), "stderr": "Codex timed out", "cli_version": _codex_version(executable), "codex_started": True}
        except OSError as error:
            platform_error = "WinError {0}".format(error.winerror) if getattr(error, "winerror", None) else error.__class__.__name__
            return {"exit_code": -1, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": "", "stderr": _public_output(str(error)), "cli_version": _codex_version(executable), "codex_started": False, "error_category": "process_launch_denied", "platform_error": platform_error}
        return {"exit_code": result.returncode, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": _public_output(result.stdout), "stderr": _public_output(result.stderr), "cli_version": _codex_version(executable), "codex_started": True}


def _public_output(value):
    text = (value or "")[:8000]
    return re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)


def _codex_version(executable):
    try:
        result = subprocess.run([executable, "--version"], shell=False, text=True, capture_output=True, timeout=10, check=False)
        return _public_output(result.stdout or result.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def run_fixed_test(workspace):
    root = Path(workspace).resolve()
    command = ["node", "--test", "tests/room-isolation.test.js"]
    safe_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC") if key in os.environ}
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=str(root), shell=False, env=safe_env, text=True, capture_output=True, timeout=20, check=False)
        return {"command_id": "node-room-isolation-test", "exit_code": result.returncode, "stdout": result.stdout[:8000], "stderr": result.stderr[:8000], "duration_ms": int((time.monotonic() - started) * 1000)}
    except subprocess.TimeoutExpired as error:
        return {"command_id": "node-room-isolation-test", "exit_code": -1, "stdout": (error.stdout or "")[:8000], "stderr": "Test timed out", "duration_ms": int((time.monotonic() - started) * 1000)}
