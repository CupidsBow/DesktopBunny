"""命令与代码执行工具（纯函数，不导入 manager）。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Any

_MAX_OUTPUT = 200_000


def run_command(
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    command = (command or "").strip()
    if not command:
        raise ValueError("command 不能为空")
    timeout = max(0.5, min(float(timeout or 30), 600))
    merged_env = os.environ.copy()
    merged_env["PYTHONIOENCODING"] = "utf-8"
    for key, value in (env or {}).items():
        merged_env[str(key)] = str(value)

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd or None,
            env=merged_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return _result(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            started,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            None,
            _as_text(exc.stdout),
            _as_text(exc.stderr),
            started,
            timed_out=True,
        )


def run_python(
    code: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    code = code or ""
    if not code.strip():
        raise ValueError("code 不能为空")
    timeout = max(0.5, min(float(timeout or 30), 600))
    merged_env = os.environ.copy()
    merged_env["PYTHONIOENCODING"] = "utf-8"
    for key, value in (env or {}).items():
        merged_env[str(key)] = str(value)

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8", newline="\n"
    )
    path = handle.name
    try:
        handle.write(code)
        handle.close()
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, path],
                cwd=cwd or None,
                env=merged_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            result = _result(
                completed.returncode,
                completed.stdout,
                completed.stderr,
                started,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            result = _result(
                None,
                _as_text(exc.stdout),
                _as_text(exc.stderr),
                started,
                timed_out=True,
            )
        result["script"] = path
        return result
    finally:
        try:
            handle.close()
        except Exception:
            pass


def _result(returncode, stdout, stderr, started, *, timed_out: bool):
    stdout = (stdout or "")[:_MAX_OUTPUT]
    stderr = (stderr or "")[:_MAX_OUTPUT]
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


__all__ = ["run_command", "run_python"]
