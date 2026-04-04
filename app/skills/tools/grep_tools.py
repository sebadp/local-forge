"""Grep tool: regex search across project files.

Provides ``grep_code`` — search for a regex pattern using ripgrep (``rg``)
with fallback to ``grep -rn`` when ripgrep is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MAX_RESULTS = 50
_MAX_OUTPUT_CHARS = 8000
_RG_AVAILABLE: bool | None = None


def _check_rg() -> bool:
    global _RG_AVAILABLE
    if _RG_AVAILABLE is None:
        _RG_AVAILABLE = shutil.which("rg") is not None
    return _RG_AVAILABLE


def register(registry: SkillRegistry, get_root: callable) -> None:  # type: ignore[type-arg]
    """Register grep_code tool. *get_root()* returns the active workspace Path."""

    async def grep_code(
        pattern: str,
        path: str = "",
        include: str = "",
        context_lines: int = 0,
        max_results: int = 50,
    ) -> str:
        """Search for a regex pattern in project files using ripgrep (or grep fallback)."""

        def _grep() -> str:
            root = get_root()
            target = (root / path).resolve() if path else root

            if not target.is_relative_to(root):
                return f"Access denied: '{path}' is outside project root."

            cap = min(max_results, _MAX_RESULTS)
            ctx = max(0, min(context_lines, 10))

            if _check_rg():
                cmd: list[str] = [
                    "rg",
                    "--no-heading",
                    "--line-number",
                    "--color",
                    "never",
                ]
                if ctx > 0:
                    cmd += ["-C", str(ctx)]
                if include:
                    for glob_pat in include.split(","):
                        g = glob_pat.strip()
                        if g:
                            cmd += ["--glob", g]
                cmd += ["--", pattern, str(target)]
            else:
                cmd = ["grep", "-rn", "--color=never"]
                if ctx > 0:
                    cmd += [f"-C{ctx}"]
                if include:
                    for glob_pat in include.split(","):
                        g = glob_pat.strip()
                        if g:
                            cmd += [f"--include={g}"]
                cmd += ["--", pattern, str(target)]

            try:
                from app.skills.tools.shell_tools import _scrubbed_env

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=str(root),
                    env=_scrubbed_env(),
                )
            except subprocess.TimeoutExpired:
                return "Search timed out (15s limit)."
            except FileNotFoundError:
                return "Error: neither rg nor grep found on this system."

            if result.returncode not in (0, 1):
                return f"Search error: {result.stderr[:500]}"

            output = result.stdout.strip()
            if not output:
                return f"No matches for pattern '{pattern}' in {path or '.'}"

            root_str = str(root) + "/"
            output = output.replace(root_str, "")

            # Global result limiting (--max-count is per-file, not global)
            lines = output.splitlines()
            if len(lines) > cap:
                output = "\n".join(lines[:cap]) + f"\n... ({len(lines)} total lines, showing first {cap})"

            if len(output) > _MAX_OUTPUT_CHARS:
                output = (
                    output[:_MAX_OUTPUT_CHARS]
                    + f"\n... (truncated, limit {_MAX_OUTPUT_CHARS} chars)"
                )

            return output

        return await asyncio.to_thread(_grep)

    registry.register_tool(
        name="grep_code",
        description=(
            "Search for a regex pattern in project files using ripgrep. "
            "Returns file:line:content matches. Supports context lines and "
            "file type filtering via include globs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Regex pattern to search for "
                        "(e.g. 'def validate_email', 'import httpx')"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to search in (relative to project root). "
                        "Empty = entire project."
                    ),
                },
                "include": {
                    "type": "string",
                    "description": (
                        "Comma-separated glob patterns to filter files "
                        "(e.g. '*.py', '*.ts,*.tsx')"
                    ),
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Number of context lines before and after each match (0-10, default 0)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matches to return (default 50)",
                },
            },
            "required": ["pattern"],
        },
        handler=grep_code,
        skill_name="code",
    )
