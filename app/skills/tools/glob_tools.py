"""Glob tool: fast file discovery by pattern.

Provides ``glob_files`` — find project files matching a glob pattern
(e.g. ``**/*.py``, ``**/test_*.ts``).  Based on ``pathlib.Path.rglob``.
"""

from __future__ import annotations

import asyncio
import logging

from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_DEFAULT_EXCLUDES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".eggs",
    }
)

_MAX_RESULTS = 200


def register(registry: SkillRegistry, get_root: callable) -> None:  # type: ignore[type-arg]
    """Register glob_files tool. *get_root()* returns the active workspace Path."""

    async def glob_files(pattern: str, directory: str = "", exclude: str = "") -> str:
        """Find files matching a glob pattern in the project."""

        def _glob() -> str:
            root = get_root()
            base = (root / directory).resolve() if directory else root

            if not base.is_relative_to(root):
                return f"Access denied: '{directory}' is outside project root."
            if not base.exists():
                return f"Directory not found: {directory or '.'}"

            user_excludes = {e.strip() for e in exclude.split(",") if e.strip()}
            all_excludes = _DEFAULT_EXCLUDES | user_excludes

            matches: list[str] = []
            for p in base.rglob(pattern):
                if any(ex in p.parts for ex in all_excludes):
                    continue
                if p.is_file():
                    matches.append(str(p.relative_to(root)))
                if len(matches) >= _MAX_RESULTS:
                    break

            if not matches:
                return f"No files matching '{pattern}' in {directory or '.'}"

            header = f"Found {len(matches)} file(s) matching '{pattern}':"
            if len(matches) >= _MAX_RESULTS:
                header += f" (limited to {_MAX_RESULTS})"
            return header + "\n" + "\n".join(sorted(matches))

        return await asyncio.to_thread(_glob)

    registry.register_tool(
        name="glob_files",
        description=(
            "Find files matching a glob pattern (e.g. '*.py', '**/test_*.ts', "
            "'**/*.md'). Returns file paths relative to project root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '*.py', '**/test_*.ts', 'src/**/*.tsx')",
                },
                "directory": {
                    "type": "string",
                    "description": "Subdirectory to search in (relative to project root). Empty = entire project.",
                },
                "exclude": {
                    "type": "string",
                    "description": (
                        "Comma-separated directory names to exclude "
                        "(added to defaults: .git, node_modules, __pycache__, .venv)"
                    ),
                },
            },
            "required": ["pattern"],
        },
        handler=glob_files,
        skill_name="code",
    )
