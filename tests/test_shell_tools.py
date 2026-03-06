"""Tests for shell_tools.py — shell execution with security controls.

Tests cover:
- _validate_command: ALLOW/DENY/ASK classification
- run_command: execution, timeout, truncation (mocked subprocess)
- manage_process: list, kill actions
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.tools.shell_tools import (
    _MAX_OUTPUT_DISPLAY,
    CommandDecision,
    _processes,
    _validate_command,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWLIST = frozenset(
    {
        "pytest",
        "ruff",
        "mypy",
        "make",
        "npm",
        "pip",
        "git",
        "cat",
        "head",
        "tail",
        "wc",
        "ls",
        "find",
        "grep",
        "echo",
        "python",
        "node",
    }
)


def _make_registry_and_settings(write_enabled: bool = True):
    """Return (registry, settings) ready for register()."""
    from unittest.mock import MagicMock

    from app.skills.registry import SkillRegistry

    registry = SkillRegistry(skills_dir="/nonexistent")
    settings = MagicMock()
    settings.agent_write_enabled = write_enabled
    settings.agent_shell_allowlist = (
        "pytest,ruff,mypy,make,npm,pip,git,cat,head,tail,wc,ls,find,grep,echo,python,node"
    )
    return registry, settings


# ---------------------------------------------------------------------------
# _validate_command tests
# ---------------------------------------------------------------------------


class TestValidateCommand:
    """Tests for _validate_command — security policy classifier."""

    # ALLOW cases
    def test_allow_pytest(self):
        assert _validate_command("pytest tests/ -v", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_git_status(self):
        assert _validate_command("git status", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_python(self):
        assert _validate_command("python -m pytest", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_ruff(self):
        assert _validate_command("ruff check app/", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_make(self):
        assert _validate_command("make test", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_ls(self):
        assert _validate_command("ls -la", _ALLOWLIST) == CommandDecision.ALLOW

    # DENY cases — denylist
    def test_deny_rm(self):
        assert _validate_command("rm -rf /", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_sudo(self):
        assert _validate_command("sudo apt install vim", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_chmod(self):
        assert _validate_command("chmod 777 /etc", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_passwd(self):
        assert _validate_command("passwd root", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_rm_rf_pattern(self):
        # Even with a different base, the pattern "rm -rf" matches
        assert _validate_command("bash -c 'rm -rf /'", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_etc_passwd_pattern(self):
        assert _validate_command("cat /etc/passwd", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_shutdown(self):
        assert _validate_command("shutdown now", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_empty_command(self):
        assert _validate_command("", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_invalid_quoting(self):
        # Unbalanced quotes → shlex fails → DENY
        assert _validate_command("echo 'unclosed", _ALLOWLIST) == CommandDecision.DENY

    # ASK cases — shell operators trigger HITL
    def test_ask_pipe_operator(self):
        assert _validate_command("ls | grep foo", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_and_operator(self):
        assert _validate_command("ls && rm bar", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_semicolon_operator(self):
        assert _validate_command("echo a; echo b", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_subshell(self):
        assert _validate_command("echo $(whoami)", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_redirect_append(self):
        assert _validate_command("echo foo >> output.txt", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_backtick(self):
        assert _validate_command("echo `date`", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_unknown_command(self):
        # Unknown command not in allowlist → ASK (HITL)
        assert _validate_command("myweirdtool --flag", _ALLOWLIST) == CommandDecision.ASK

    def test_ask_curl(self):
        assert _validate_command("curl https://example.com", _ALLOWLIST) == CommandDecision.ASK

    # Full path commands (base_cmd resolved)
    def test_allow_full_path_python(self):
        assert _validate_command("/usr/bin/python -m pytest", _ALLOWLIST) == CommandDecision.ALLOW

    def test_deny_full_path_rm(self):
        assert _validate_command("/bin/rm -rf /tmp/foo", _ALLOWLIST) == CommandDecision.DENY


# ---------------------------------------------------------------------------
# run_command tests (mocked subprocess)
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests for run_command via registry (mocked subprocess)."""

    async def test_run_command_success(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello world\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await registry.execute_tool(
                __import__("app.skills.models", fromlist=["ToolCall"]).ToolCall(
                    name="run_command", arguments={"command": "pytest tests/"}
                )
            )

        assert result.success
        assert "hello world" in result.content
        assert "Exit code: 0" in result.content

    async def test_run_command_disabled(self):
        registry, settings = _make_registry_and_settings(write_enabled=False)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="run_command", arguments={"command": "pytest tests/"})
        )

        assert result.success  # handler always returns, no exception
        assert "disabled" in result.content.lower() or "AGENT_WRITE_ENABLED" in result.content

    async def test_run_command_denied(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="run_command", arguments={"command": "rm -rf /"})
        )

        assert result.success
        assert "blocked" in result.content.lower()

    async def test_run_command_ask_returns_hitl_message(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="run_command", arguments={"command": "curl https://example.com"})
        )

        assert result.success
        # Unknown command → ASK → returns message about approval required
        assert "allowlist" in result.content.lower() or "approval" in result.content.lower()

    async def test_run_command_timeout(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            from app.skills.models import ToolCall

            result = await registry.execute_tool(
                ToolCall(name="run_command", arguments={"command": "pytest tests/", "timeout": 1})
            )

        assert result.success
        assert "timed out" in result.content.lower()

    async def test_run_command_output_truncated(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        # Output larger than _MAX_OUTPUT_DISPLAY
        big_output = b"x" * (_MAX_OUTPUT_DISPLAY + 1000)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(big_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            from app.skills.models import ToolCall

            result = await registry.execute_tool(
                ToolCall(name="run_command", arguments={"command": "cat bigfile.txt"})
            )

        assert result.success
        assert "truncated" in result.content or len(result.content) < len(big_output) + 500

    async def test_run_command_nonzero_exit_code(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error: file not found\n"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            from app.skills.models import ToolCall

            result = await registry.execute_tool(
                ToolCall(name="run_command", arguments={"command": "pytest tests/missing.py"})
            )

        assert result.success
        assert "Exit code: 1" in result.content
        assert "error: file not found" in result.content

    async def test_run_command_no_output(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            from app.skills.models import ToolCall

            result = await registry.execute_tool(
                ToolCall(name="run_command", arguments={"command": "make clean"})
            )

        assert result.success
        assert "no output" in result.content.lower()


# ---------------------------------------------------------------------------
# manage_process tests
# ---------------------------------------------------------------------------


class TestManageProcess:
    """Tests for manage_process — background process management."""

    def _setup_fake_process(self, exit_code: int | None = None) -> tuple:
        """Create a fake _ProcessInfo and insert it into the module registry."""
        import time

        from app.skills.tools.shell_tools import _ProcessInfo

        fake_proc = MagicMock()
        fake_proc.returncode = exit_code

        pid = "test1234"
        info = _ProcessInfo(
            proc=fake_proc,
            started_at=time.monotonic() - 10,
            command="pytest tests/",
            exit_code=exit_code,
        )
        _processes[pid] = info
        return pid, info, fake_proc

    def teardown_method(self):
        """Clean up _processes after each test."""
        _processes.clear()

    async def test_manage_process_list_empty(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="manage_process", arguments={"action": "list"})
        )
        assert result.success
        assert "no background" in result.content.lower()

    async def test_manage_process_list_with_running_process(self):
        pid, _, _ = self._setup_fake_process(exit_code=None)

        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="manage_process", arguments={"action": "list"})
        )
        assert result.success
        assert pid in result.content
        assert "running" in result.content.lower()

    async def test_manage_process_list_with_completed_process(self):
        pid, _, _ = self._setup_fake_process(exit_code=0)

        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="manage_process", arguments={"action": "list"})
        )
        assert result.success
        assert "exit=0" in result.content

    async def test_manage_process_kill_running(self):
        pid, info, fake_proc = self._setup_fake_process(exit_code=None)
        fake_proc.returncode = None
        fake_proc.terminate = MagicMock()
        fake_proc.kill = MagicMock()
        info.exit_code = None  # mark as still running

        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="manage_process", arguments={"action": "kill", "process_id": pid})
        )
        assert result.success
        assert (
            "terminated" in result.content.lower()
            or "killed" in result.content.lower()
            or pid in result.content
        )
        fake_proc.terminate.assert_called_once()

    async def test_manage_process_kill_already_completed(self):
        pid, info, fake_proc = self._setup_fake_process(exit_code=0)

        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(name="manage_process", arguments={"action": "kill", "process_id": pid})
        )
        assert result.success
        assert "already completed" in result.content.lower()

    async def test_manage_process_unknown_pid(self):
        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(
                name="manage_process",
                arguments={"action": "kill", "process_id": "nonexistent_pid"},
            )
        )
        assert result.success
        assert "not found" in result.content.lower()

    async def test_manage_process_unknown_action(self):
        # With a valid process_id, the unknown action check is reached
        pid, _, _ = self._setup_fake_process(exit_code=0)

        registry, settings = _make_registry_and_settings(write_enabled=True)
        from app.skills.tools.shell_tools import register

        register(registry, settings)

        from app.skills.models import ToolCall

        result = await registry.execute_tool(
            ToolCall(
                name="manage_process",
                arguments={"action": "fly", "process_id": pid},
            )
        )
        assert result.success
        assert (
            "unknown action" in result.content.lower()
            or "unknown" in result.content.lower()
            or "use:" in result.content.lower()
        )


# ---------------------------------------------------------------------------
# Argument-level security validation
# ---------------------------------------------------------------------------


class TestArgumentValidation:
    """Argument-level security checks for allowlisted interpreters and package managers."""

    def test_deny_python_dash_c(self):
        assert _validate_command("python -c 'print(1)'", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_python3_dash_c(self):
        # python3 resolves to base_cmd="python3"; not in allowlist by default but
        # if added, the check must fire. Use a custom allowlist for this test.
        allowlist = frozenset(_ALLOWLIST | {"python3"})
        assert _validate_command("python3 -c 'import os'", allowlist) == CommandDecision.DENY

    def test_deny_node_dash_e(self):
        assert _validate_command("node -e 'console.log(1)'", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_node_eval_flag(self):
        assert _validate_command("node --eval 'console.log(1)'", _ALLOWLIST) == CommandDecision.DENY

    def test_allow_python_dash_m(self):
        # -m imports a module by name, not arbitrary code — must remain ALLOW
        assert _validate_command("python -m pytest", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_python_script(self):
        # Running a .py file (no -c) must remain ALLOW
        assert _validate_command("python scripts/run_eval.py", _ALLOWLIST) == CommandDecision.ALLOW

    def test_allow_full_path_python_dash_m(self):
        assert _validate_command("/usr/bin/python -m pytest", _ALLOWLIST) == CommandDecision.ALLOW

    def test_ask_pip_install(self):
        # pip is no longer in the default allowlist, but if added via env,
        # 'install' subcommand must require HITL
        allowlist = frozenset(_ALLOWLIST | {"pip"})
        assert _validate_command("pip install requests", allowlist) == CommandDecision.ASK

    def test_allow_pip_show(self):
        allowlist = frozenset(_ALLOWLIST | {"pip"})
        assert _validate_command("pip show requests", allowlist) == CommandDecision.ALLOW

    def test_allow_pip_list(self):
        allowlist = frozenset(_ALLOWLIST | {"pip"})
        assert _validate_command("pip list", allowlist) == CommandDecision.ALLOW

    def test_ask_pip_download(self):
        allowlist = frozenset(_ALLOWLIST | {"pip"})
        assert _validate_command("pip download requests", allowlist) == CommandDecision.ASK

    def test_deny_ssh_key_pattern(self):
        assert _validate_command("cat /home/user/.ssh/id_rsa", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_etc_sudoers(self):
        assert _validate_command("cat /etc/sudoers", _ALLOWLIST) == CommandDecision.DENY

    def test_deny_etc_crontab(self):
        assert _validate_command("grep -r '' /etc/crontab", _ALLOWLIST) == CommandDecision.DENY
