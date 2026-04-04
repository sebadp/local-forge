"""Shell execution tools for agentic sessions.

Provides sandboxed shell command execution with defense-in-depth security:
- Layer 1: Gate — requires AGENT_WRITE_ENABLED=true
- Layer 2: Command validation — denylist, allowlist, HITL for unknowns
- Layer 3: Execution sandbox — shell=False, stdin=DEVNULL, cwd=PROJECT_ROOT
- Layer 4: Resource limits — max processes, output truncation, timeout, GC

Tools registered:
- run_command: Execute a shell command synchronously or in background
- manage_process: List/poll/log/kill background processes
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shlex
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# --- Credential scrubbing for subprocesses (Plan 60) ---

_SCRUB_EXACT: frozenset[str] = frozenset({
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
    "GITHUB_TOKEN",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "AUDIT_HMAC_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "NGROK_AUTHTOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
})

_SCRUB_SUFFIXES: tuple[str, ...] = (
    "_SECRET", "_TOKEN", "_KEY", "_PASSWORD", "_CREDENTIAL",
    "_API_KEY", "_ACCESS_KEY", "_SECRET_KEY",
)

# Keys that end with a scrub suffix but should be preserved
_SCRUB_KEEP: frozenset[str] = frozenset({
    "TERM_SESSION_ID",
    "COLORTERM",
})


def _scrubbed_env() -> dict[str, str]:
    """Return a copy of os.environ with sensitive variables removed."""
    env = os.environ.copy()
    to_remove: list[str] = []
    for key in env:
        if key in _SCRUB_KEEP:
            continue
        if key in _SCRUB_EXACT:
            to_remove.append(key)
        elif any(key.upper().endswith(s) for s in _SCRUB_SUFFIXES):
            to_remove.append(key)
    for key in to_remove:
        del env[key]
    return env

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------

_DENYLIST = frozenset(
    {
        "rm",
        "sudo",
        "chmod",
        "chown",
        "mkfs",
        "dd",
        "shutdown",
        "reboot",
        "systemctl",
        "mount",
        "umount",
        "fdisk",
        "parted",
        "useradd",
        "userdel",
        "passwd",
        "su",
    }
)

_DANGEROUS_PATTERNS = frozenset(
    {
        "rm -rf",
        "> /dev/",
        ":()",
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "/etc/crontab",
        "/.ssh/",
        "id_rsa",
        "curl | sh",
        "curl | bash",
        "wget | sh",
        "wget | bash",
    }
)

_SHELL_OPERATORS = frozenset({"|", ">>", "&&", "||", ";", "$(", "`"})

# Flags that execute inline code strings — blocked for allowlisted interpreters
_CODE_EXEC_FLAGS = frozenset({"-c", "-e", "--eval", "--exec"})

# Subcommands that install packages — require HITL even for allowlisted package managers
_PACKAGE_MGMT_SUBCMDS = frozenset({"install", "download", "wheel"})

# Allowlisted commands for which argument-level checks apply
_CODE_EXEC_CMDS = frozenset({"python", "python3", "node", "ruby", "perl", "php"})
_PACKAGE_MGMT_CMDS = frozenset({"pip", "pip3", "npm", "yarn"})

# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_PROCESSES = 5
_MAX_OUTPUT_BYTES = 50_000  # truncate internal buffer at 50KB
_MAX_OUTPUT_DISPLAY = 4_000  # chars returned to LLM
_PROCESS_MAX_AGE = 1800  # kill after 30 minutes
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 300


class CommandDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class _ProcessInfo:
    proc: asyncio.subprocess.Process
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    started_at: float = 0.0
    command: str = ""
    session_id: str = ""
    phone_number: str = ""
    exit_code: int | None = None
    last_poll_offset: int = 0


# Module-level registry of background processes
_processes: dict[str, _ProcessInfo] = {}


def _validate_command(command: str, allowlist: frozenset[str]) -> CommandDecision:
    """Classify a command as ALLOW, DENY, or ASK (HITL required)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return CommandDecision.DENY

    if not tokens:
        return CommandDecision.DENY

    base_cmd = Path(tokens[0]).name  # handle /usr/bin/python → python

    # 1. Denylist (base command + dangerous patterns)
    if base_cmd in _DENYLIST:
        return CommandDecision.DENY
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in command:
            return CommandDecision.DENY

    # 2. Shell operator check → HITL
    for op in _SHELL_OPERATORS:
        if op in command:
            return CommandDecision.ASK

    # 3. Allowlist + argument-level validation
    if base_cmd in allowlist:
        # Block inline code-execution flags for interpreter commands
        if base_cmd in _CODE_EXEC_CMDS:
            for tok in tokens[1:]:
                if tok in _CODE_EXEC_FLAGS:
                    return CommandDecision.DENY
        # Package install subcommands require HITL even for allowlisted managers.
        # Scan all tokens (not just tokens[1]) to catch flags before the subcommand,
        # e.g. `pip --quiet install pkg` or `npm --verbose install pkg`.
        if base_cmd in _PACKAGE_MGMT_CMDS:
            if any(tok in _PACKAGE_MGMT_SUBCMDS for tok in tokens[1:]):
                return CommandDecision.ASK
        return CommandDecision.ALLOW

    # 4. Unknown → HITL
    return CommandDecision.ASK


def _truncate(text: str, max_chars: int = _MAX_OUTPUT_DISPLAY) -> str:
    if len(text) <= max_chars:
        return text
    return f"... (truncated, showing last {max_chars} chars)\n" + text[-max_chars:]


async def gc_stale_processes() -> None:
    """Kill and remove stale background processes. Called periodically."""
    now = time.monotonic()
    to_remove: list[str] = []

    for pid, info in _processes.items():
        age = now - info.started_at
        if info.exit_code is not None and age > 600:
            # Completed and older than 10 min — clean up
            to_remove.append(pid)
        elif info.exit_code is None and age > _PROCESS_MAX_AGE:
            # Still running after 30 min — kill
            logger.warning(
                "agent.process.gc",
                extra={
                    "process_id": pid,
                    "command": info.command,
                    "age_s": int(age),
                    "action": "kill_stale",
                },
            )
            try:
                info.proc.kill()
            except ProcessLookupError:
                pass
            info.exit_code = -9
            to_remove.append(pid)

    for pid in to_remove:
        _processes.pop(pid, None)

    if to_remove:
        logger.info("agent.process.gc: cleaned %d processes", len(to_remove))


def register(registry: SkillRegistry, settings: Settings) -> None:
    """Register shell execution tools in the skill registry."""

    allowlist = frozenset(
        cmd.strip() for cmd in settings.agent_shell_allowlist.split(",") if cmd.strip()
    )

    async def run_command(
        command: str, timeout: int = _DEFAULT_TIMEOUT, background: bool = False
    ) -> str:
        """Execute a shell command in the project directory.

        Requires AGENT_WRITE_ENABLED=true.
        Commands in the allowlist run directly. Unknown commands require user approval.
        Dangerous commands (rm, sudo, etc.) are blocked outright.
        """
        if not settings.agent_write_enabled:
            return "Error: Shell execution disabled. Set AGENT_WRITE_ENABLED=true in .env."

        timeout = min(max(timeout, 1), _MAX_TIMEOUT)
        decision = _validate_command(command, allowlist)

        # Log the attempt
        logger.info(
            "agent.shell.validate",
            extra={
                "command": command,
                "decision": decision.value,
                "background": background,
            },
        )

        if decision == CommandDecision.DENY:
            try:
                base = shlex.split(command)[0]
            except (ValueError, IndexError):
                base = command[:50]
            return f"🚫 Command blocked: `{base}` is not allowed for security reasons."

        if decision == CommandDecision.ASK:
            # Return a message that the agent should use with request_user_approval
            return (
                f"⚠️ Command `{command}` is not in the allowlist and requires user approval.\n"
                f"Please call request_user_approval with this command before executing.\n"
                f"To execute after approval, call run_command again."
            )

        # --- ALLOW: execute ---
        try:
            tokens = shlex.split(command)
        except ValueError as e:
            return f"Error parsing command: {e}"

        if background:
            if len(_processes) >= _MAX_CONCURRENT_PROCESSES:
                return (
                    f"Too many background processes ({len(_processes)}/{_MAX_CONCURRENT_PROCESSES}). "
                    "Use manage_process to kill one first."
                )
            return await _start_background(tokens, command)

        return await _run_sync(tokens, command, timeout)

    async def _run_sync(tokens: list[str], command: str, timeout: int) -> str:
        """Run a command synchronously, waiting for completion."""
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *tokens,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(_PROJECT_ROOT),
                env=_scrubbed_env(),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except ProcessLookupError:
                pass
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "agent.shell.timeout",
                extra={"command": command, "timeout": timeout, "duration_ms": duration_ms},
            )
            return f"⏰ Command timed out after {timeout}s. Consider increasing timeout or using background=true."
        except FileNotFoundError:
            return f"Error: command `{tokens[0]}` not found."
        except Exception as e:
            return f"Error executing command: {e}"

        duration_ms = int((time.monotonic() - started) * 1000)
        exit_code = proc.returncode or 0

        stdout = (stdout_bytes or b"")[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"")[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        logger.info(
            "agent.shell.execute",
            extra={
                "command": command,
                "decision": "allow",
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_bytes": len(stdout_bytes or b""),
                "stderr_bytes": len(stderr_bytes or b""),
            },
        )

        parts = [f"Exit code: {exit_code} | Duration: {duration_ms}ms"]
        if stdout.strip():
            parts.append(f"stdout:\n{_truncate(stdout.strip())}")
        if stderr.strip():
            parts.append(f"stderr:\n{_truncate(stderr.strip())}")
        if not stdout.strip() and not stderr.strip():
            parts.append("(no output)")

        return "\n".join(parts)

    async def _start_background(tokens: list[str], command: str) -> str:
        """Start a command in the background, return a process_id."""
        process_id = hashlib.md5(f"{command}-{time.monotonic()}".encode()).hexdigest()[:8]

        try:
            proc = await asyncio.create_subprocess_exec(
                *tokens,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(_PROJECT_ROOT),
                env=_scrubbed_env(),
            )
        except FileNotFoundError:
            return f"Error: command `{tokens[0]}` not found."
        except Exception as e:
            return f"Error starting background process: {e}"

        _processes[process_id] = _ProcessInfo(
            proc=proc,
            started_at=time.monotonic(),
            command=command,
        )

        logger.info(
            "agent.shell.background",
            extra={"command": command, "process_id": process_id},
        )

        return (
            f"🔄 Background process started.\n"
            f"Process ID: `{process_id}`\n"
            f"Use manage_process(action='poll', process_id='{process_id}') to check status."
        )

    async def manage_process(action: str, process_id: str = "", limit: int = 50) -> str:
        """Manage background processes: list, poll, log, or kill.

        Actions:
        - list: Show all active/completed background processes
        - poll: Get new output + exit status for a specific process
        - log: Get last N lines of output (default 50)
        - kill: Terminate a process
        """
        action = action.strip().lower()

        if action == "list":
            if not _processes:
                return "No background processes."
            lines = ["Background processes:"]
            for pid, info in _processes.items():
                age = int(time.monotonic() - info.started_at)
                status = f"exit={info.exit_code}" if info.exit_code is not None else "running"
                lines.append(f"  {pid}: `{info.command[:60]}` ({status}, {age}s)")
            return "\n".join(lines)

        if not process_id or process_id not in _processes:
            available = ", ".join(_processes.keys()) if _processes else "none"
            return f"Process `{process_id}` not found. Active: {available}"

        info = _processes[process_id]

        if action == "poll":
            # Check if process has completed
            if info.exit_code is None and info.proc.returncode is not None:
                info.exit_code = info.proc.returncode
                # Read remaining output
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        info.proc.communicate(), timeout=1
                    )
                    if stdout_bytes:
                        info.stdout_lines.extend(
                            stdout_bytes[:_MAX_OUTPUT_BYTES]
                            .decode("utf-8", errors="replace")
                            .splitlines()
                        )
                    if stderr_bytes:
                        info.stderr_lines.extend(
                            stderr_bytes[:_MAX_OUTPUT_BYTES]
                            .decode("utf-8", errors="replace")
                            .splitlines()
                        )
                except (TimeoutError, Exception):
                    pass

            # Return new lines since last poll
            new_stdout = info.stdout_lines[info.last_poll_offset :]
            info.last_poll_offset = len(info.stdout_lines)

            status = "running" if info.exit_code is None else f"completed (exit={info.exit_code})"
            parts = [f"Status: {status}"]
            if new_stdout:
                output = "\n".join(new_stdout[-limit:])
                parts.append(f"New output ({len(new_stdout)} lines):\n{_truncate(output)}")
            else:
                parts.append("(no new output)")
            if info.stderr_lines:
                stderr = "\n".join(info.stderr_lines[-20:])
                parts.append(f"stderr:\n{_truncate(stderr)}")
            return "\n".join(parts)

        if action == "log":
            limit = min(max(limit, 1), 200)
            all_output = info.stdout_lines + info.stderr_lines
            if not all_output:
                return f"Process `{process_id}`: no output yet."
            tail = all_output[-limit:]
            status = "running" if info.exit_code is None else f"exit={info.exit_code}"
            return f"Process `{process_id}` ({status}), last {len(tail)} lines:\n" + "\n".join(tail)

        if action == "kill":
            if info.exit_code is not None:
                return f"Process `{process_id}` already completed (exit={info.exit_code})."
            try:
                info.proc.terminate()
                await asyncio.sleep(0.5)
                if info.proc.returncode is None:
                    info.proc.kill()
                info.exit_code = info.proc.returncode if info.proc.returncode is not None else -15
            except ProcessLookupError:
                info.exit_code = -1
            logger.info(
                "agent.process.killed", extra={"process_id": process_id, "command": info.command}
            )
            return f"✅ Process `{process_id}` terminated."

        return f"Unknown action `{action}`. Use: list, poll, log, kill."

    # --- Register tools ---

    registry.register_tool(
        name="run_command",
        description=(
            "Execute a shell command in the project directory. "
            "Allowed commands (pytest, ruff, mypy, git, etc.) run directly. "
            "Unknown commands require user approval. "
            "Dangerous commands (rm, sudo) are blocked. "
            "Requires AGENT_WRITE_ENABLED=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute, e.g. 'pytest tests/ -v'",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Max seconds to wait (default {_DEFAULT_TIMEOUT}, max {_MAX_TIMEOUT}). Ignored for background.",
                    "default": _DEFAULT_TIMEOUT,
                },
                "background": {
                    "type": "boolean",
                    "description": "If true, run in background and return a process_id for polling.",
                    "default": False,
                },
            },
            "required": ["command"],
        },
        handler=run_command,
        skill_name="shell",
    )

    registry.register_tool(
        name="manage_process",
        description=(
            "Manage background processes started with run_command(background=true). "
            "Actions: 'list' (show all), 'poll' (new output + status), 'log' (tail output), 'kill' (terminate)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform: list, poll, log, kill",
                    "enum": ["list", "poll", "log", "kill"],
                },
                "process_id": {
                    "type": "string",
                    "description": "Process ID (required for poll, log, kill)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines for 'log' action (default 50, max 200)",
                    "default": 50,
                },
            },
            "required": ["action"],
        },
        handler=manage_process,
        skill_name="shell",
    )
