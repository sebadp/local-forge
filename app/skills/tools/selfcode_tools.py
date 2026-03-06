from __future__ import annotations

import asyncio
import difflib
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings
    from app.llm.client import OllamaClient
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Resolve project root once at import time (localforge-assistant/)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SENSITIVE = {
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "whatsapp_verify_token",
    "ngrok_authtoken",
    "github_token",
    "langfuse_secret_key",
    "langfuse_public_key",
    "audit_hmac_key",
    "telegram_bot_token",
    "telegram_webhook_secret",
}

_BLOCKED_NAME_PATTERNS = {".env", "secret", "token", "password", ".key", ".pem"}
_BLOCKED_EXT = {".pyc", ".pyo", ".db", ".sqlite", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".tar"}

# Config files that the agent must never overwrite (security boundaries)
_BLOCKED_CONFIG_FILES = {
    "mcp_servers.json",
    "security_policies.yaml",
    "audit_trail.jsonl",
}


def _is_safe_path(path: Path) -> bool:
    """Return True if path is within PROJECT_ROOT and not a sensitive file."""
    try:
        resolved = path.resolve()
    except Exception:
        return False

    if not resolved.is_relative_to(_PROJECT_ROOT):
        return False

    name_lower = resolved.name.lower()
    for pattern in _BLOCKED_NAME_PATTERNS:
        if pattern in name_lower:
            return False

    # Also block if any parent component is .env
    for part in resolved.parts:
        if part == ".env":
            return False

    return True


def register(
    registry: SkillRegistry,
    settings: Settings,
    ollama_client: OllamaClient | None = None,
    vec_available: bool = False,
) -> None:
    async def get_version_info() -> str:
        def _collect() -> str:
            lines = []

            # Git commit
            try:
                result = subprocess.run(
                    ["git", "log", "-1", "--pretty=format:%H %s %ai"],
                    capture_output=True,
                    text=True,
                    cwd=str(_PROJECT_ROOT),
                )
                if result.returncode == 0 and result.stdout.strip():
                    lines.append(f"Last commit: {result.stdout.strip()}")
            except Exception as e:
                lines.append(f"Git log unavailable: {e}")

            # Git branch
            try:
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True,
                    text=True,
                    cwd=str(_PROJECT_ROOT),
                )
                if result.returncode == 0:
                    lines.append(f"Branch: {result.stdout.strip()}")
            except Exception as e:
                lines.append(f"Git branch unavailable: {e}")

            # Python version
            lines.append(f"Python: {sys.version}")

            # Models from settings
            lines.append(f"Chat model: {settings.ollama_model}")
            lines.append(f"Embedding model: {settings.embedding_model}")
            lines.append(f"Project root: {_PROJECT_ROOT}")

            return "\n".join(lines)

        return await asyncio.to_thread(_collect)

    async def read_source_file(path: str) -> str:
        def _read() -> str:
            target = (_PROJECT_ROOT / path).resolve()

            if not _is_safe_path(target):
                return f"Access denied: '{path}' is outside project root or is a sensitive file."

            if not target.exists():
                return f"File not found: {path}"

            if not target.is_file():
                return f"Not a file: {path}"

            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"Error reading file: {e}"

            # Add line numbers
            lines = content.splitlines()
            numbered = "\n".join(f"{i + 1:4d}  {line}" for i, line in enumerate(lines))

            # Truncate if too long
            if len(numbered) > 5000:
                numbered = numbered[:5000] + f"\n... (truncated, {len(lines)} total lines)"

            return f"=== {path} ===\n{numbered}"

        return await asyncio.to_thread(_read)

    async def list_source_files(directory: str = "") -> str:
        def _list() -> str:
            target = (_PROJECT_ROOT / directory).resolve() if directory else _PROJECT_ROOT

            if not target.is_relative_to(_PROJECT_ROOT):
                return f"Access denied: '{directory}' is outside project root."

            if not target.exists():
                return f"Directory not found: {directory or '.'}"

            if not target.is_dir():
                return f"Not a directory: {directory or '.'}"

            EXCLUDED = {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache"}
            EXCLUDED_EXTS = {".pyc", ".pyo"}

            lines = [f"Contents of {directory or '.'}:"]

            def _walk(d: Path, prefix: str) -> None:
                try:
                    entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
                except PermissionError:
                    return
                for entry in entries:
                    if entry.name in EXCLUDED or entry.name.startswith("."):
                        continue
                    if entry.is_file() and entry.suffix in EXCLUDED_EXTS:
                        continue
                    if entry.is_dir():
                        lines.append(f"{prefix}{entry.name}/")
                        _walk(entry, prefix + "  ")
                    else:
                        lines.append(f"{prefix}{entry.name}")

            _walk(target, "  ")

            result = "\n".join(lines)
            if len(result) > 5000:
                result = result[:5000] + "\n... (truncated)"
            return result

        return await asyncio.to_thread(_list)

    async def get_runtime_config() -> str:
        lines = ["Runtime configuration (sensitive fields hidden):"]
        try:
            for field_name, _ in settings.model_fields.items():
                if field_name in _SENSITIVE:
                    lines.append(f"  {field_name}: ***hidden***")
                else:
                    value = getattr(settings, field_name, None)
                    lines.append(f"  {field_name}: {value}")
        except Exception as e:
            lines.append(f"Error reading config: {e}")
        return "\n".join(lines)

    async def get_system_health() -> str:
        parts = []

        # Ollama ping
        if ollama_client:
            try:
                await ollama_client.embed(["health_check"], model=settings.embedding_model)
                parts.append("Ollama: OK")
            except Exception as e:
                parts.append(f"Ollama: ERROR ({e})")
        else:
            parts.append("Ollama: client not configured")

        # Vector search
        parts.append(
            f"Vector search (sqlite-vec): {'available' if vec_available else 'not available'}"
        )

        # Data directories
        data_dirs = ["data", "data/memory", "data/memory/snapshots"]
        for d in data_dirs:
            p = _PROJECT_ROOT / d
            parts.append(f"Dir '{d}': {'exists' if p.exists() else 'missing'}")

        # Skills directory
        skills_path = _PROJECT_ROOT / settings.skills_dir
        parts.append(
            f"Skills dir '{settings.skills_dir}': {'exists' if skills_path.exists() else 'missing'}"
        )

        return "System health:\n" + "\n".join(f"  {p}" for p in parts)

    async def search_source_code(pattern: str) -> str:
        def _search() -> str:
            if not pattern or len(pattern) > 200:
                return "Invalid pattern."

            try:
                result = subprocess.run(
                    [
                        "grep",
                        "-rn",
                        "--include=*.py",
                        "--include=*.md",
                        "--",
                        pattern,
                        str(_PROJECT_ROOT),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                return "Search timed out."
            except FileNotFoundError:
                return "grep not available on this system."
            except Exception as e:
                return f"Search error: {e}"

            output = result.stdout or result.stderr or "(no matches)"

            # Strip the project root prefix for readability
            root_str = str(_PROJECT_ROOT) + "/"
            output = output.replace(root_str, "")

            # Limit to 60 lines
            lines = output.splitlines()
            if len(lines) > 60:
                lines = lines[:60]
                lines.append(
                    f"... (showing 60 of {len(lines) + (len(output.splitlines()) - 60)} matches)"
                )

            return "\n".join(lines)

        return await asyncio.to_thread(_search)

    async def get_skill_details(skill_name: str) -> str:
        skill = registry.get_skill(skill_name)
        if not skill:
            available = [s.name for s in registry.list_skills()]
            return f"Skill '{skill_name}' not found. Available skills: {', '.join(available) or 'none'}"

        tools = registry.get_tools_for_skill(skill_name)

        lines = [
            f"Skill: {skill.name}",
            f"Version: {skill.version}",
            f"Description: {skill.description}",
            "",
            "Tools:",
        ]
        for t in tools:
            td = registry._tools.get(t.name)
            if td:
                lines.append(f"  - {td.name}: {td.description}")
            else:
                lines.append(f"  - {t.name}")

        if skill.instructions:
            lines.append("")
            lines.append("Instructions:")
            lines.append(skill.instructions)

        return "\n".join(lines)

    async def get_recent_logs(lines: int = 100) -> str:
        def _read_logs() -> str:
            if lines > 500:
                return "Request too large. Max lines is 500."

            log_path = _PROJECT_ROOT / "data" / "localforge.log"
            if not log_path.exists():
                return "Log file not found at data/localforge.log"

            try:
                result = subprocess.run(
                    ["tail", "-n", str(max(1, lines)), str(log_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    output = result.stdout.strip()
                    return output if output else "Log file is empty."
                return f"Error reading logs: {result.stderr}"
            except subprocess.TimeoutExpired:
                return "Reading logs timed out."
            except FileNotFoundError:
                return "tail command not available on this system."
            except Exception as e:
                return f"Error: {e}"

        return await asyncio.to_thread(_read_logs)

    async def write_source_file(path: str, content: str) -> str:
        """Write content to a file within the project. Creates the file if it doesn't exist.

        Requires AGENT_WRITE_ENABLED=true in config.
        Safety: Only allows writing within PROJECT_ROOT. Blocks sensitive files and binary extensions.
        """
        if not settings.agent_write_enabled:
            return "Error: Write operations are disabled. Set AGENT_WRITE_ENABLED=true in .env to enable."

        target = (_PROJECT_ROOT / path).resolve()

        if not _is_safe_path(target):
            return f"Blocked: '{path}' is outside the project root or is a sensitive file."

        if target.name.lower() in _BLOCKED_CONFIG_FILES:
            return f"Blocked: '{path}' is a protected configuration file and cannot be overwritten."

        if target.suffix.lower() in _BLOCKED_EXT:
            return f"Blocked: Cannot write binary or database file ({target.suffix})"

        def _write() -> str:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return f"✅ Written {len(content)} chars to {path}"
            except Exception as e:
                return f"Error writing file: {e}"

        logger.info("Agent write_source_file: %s (%d chars)", path, len(content))
        return await asyncio.to_thread(_write)

    async def preview_patch(path: str, search: str, replace: str) -> str:
        """Preview a targeted text replacement in an existing source file.

        Finds the FIRST occurrence of `search` and replaces it with `replace` in memory.
        Returns a unified diff showing what WOULD change. Does NOT modify the file.
        Use this to verify changes before apply_patch.
        """
        target = (_PROJECT_ROOT / path).resolve()

        if not _is_safe_path(target):
            return f"Blocked: '{path}' is outside the project root or is a sensitive file."

        if target.suffix.lower() in _BLOCKED_EXT:
            return f"Blocked: Cannot patch binary or database file ({target.suffix})"

        def _preview() -> str:
            if not target.exists():
                return f"Error: File '{path}' does not exist."

            try:
                text = target.read_text(encoding="utf-8")
            except Exception as e:
                return f"Error reading file: {e}"

            if search not in text:
                snippet = text[:300] + ("..." if len(text) > 300 else "")
                return (
                    f"Error: Search string not found in '{path}'.\n"
                    f"Use read_source_file to check the exact current content.\n"
                    f"File starts with:\n{snippet}"
                )

            new_text = text.replace(search, replace, 1)

            original_lines = text.splitlines(keepends=True)
            new_lines = new_text.splitlines(keepends=True)
            diff = "".join(
                difflib.unified_diff(original_lines, new_lines, fromfile=path, tofile=path, n=3)
            )

            if not diff.strip():
                return "The expected replacement does not actually change the file content (search == replace)."

            return (
                f"🔍 **Preview of changes for `{path}`**:\n"
                f"```diff\n{diff}```\n"
                f"If you are confident in this change, call `apply_patch` with the exact same arguments."
            )

        logger.info("Agent preview_patch: %s (search=%d chars)", path, len(search))
        return await asyncio.to_thread(_preview)

    async def apply_patch(path: str, search: str, replace: str) -> str:
        """Apply a targeted text replacement in a source file.

        Finds the FIRST occurrence of `search` in the file and replaces it with `replace`.
        Safer than full file rewrites for small edits.
        Requires AGENT_WRITE_ENABLED=true in config.
        """
        if not settings.agent_write_enabled:
            return "Error: Write operations are disabled. Set AGENT_WRITE_ENABLED=true in .env to enable."

        target = (_PROJECT_ROOT / path).resolve()

        if not _is_safe_path(target):
            return f"Blocked: '{path}' is outside the project root or is a sensitive file."

        if target.name.lower() in _BLOCKED_CONFIG_FILES:
            return f"Blocked: '{path}' is a protected configuration file and cannot be modified."

        if target.suffix.lower() in _BLOCKED_EXT:
            return f"Blocked: Cannot patch binary or database file ({target.suffix})"

        def _patch() -> str:
            if not target.exists():
                return f"Error: File '{path}' does not exist. Use write_source_file to create it."

            try:
                text = target.read_text(encoding="utf-8")
            except Exception as e:
                return f"Error reading file: {e}"

            if search not in text:
                # Give the LLM a useful hint to diagnose what went wrong
                snippet = text[:300] + ("..." if len(text) > 300 else "")
                return (
                    f"Error: Search string not found in '{path}'.\n"
                    f"Use read_source_file to check the exact current content.\n"
                    f"File starts with:\n{snippet}"
                )

            new_text = text.replace(search, replace, 1)
            try:
                target.write_text(new_text, encoding="utf-8")
            except Exception as e:
                return f"Error writing file: {e}"

            return f"✅ Patched '{path}': replaced {len(search)} chars with {len(replace)} chars."

        logger.info("Agent apply_patch: %s (search=%d chars)", path, len(search))
        return await asyncio.to_thread(_patch)

    async def get_file_outline(path: str) -> str:
        """Return a structural outline of a source file (functions, classes, line numbers).

        Uses AST for .py files; falls back to regex for other text files.
        Does NOT read file content into context — only structure.
        """
        import ast
        import re as _re

        target = (_PROJECT_ROOT / path).resolve()

        if not _is_safe_path(target):
            return f"Blocked: '{path}' is outside the project root."
        if not target.exists():
            return f"Error: File '{path}' does not exist."
        if not target.is_file():
            return f"Error: '{path}' is not a file."

        def _outline() -> str:
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"Error reading file: {e}"

            lines = text.splitlines()
            total_lines = len(lines)
            items: list[tuple[int, str]] = []

            if target.suffix == ".py":
                try:
                    tree = ast.parse(text, filename=str(target))
                    # Iterate only top-level Module children to avoid double-listing methods
                    for node in tree.body:
                        if isinstance(node, ast.ClassDef):
                            end = getattr(node, "end_lineno", node.lineno)
                            items.append(
                                (node.lineno, f"  class {node.name}  [L{node.lineno}-{end}]")
                            )
                            # Iterate direct class members only (not nested via ast.walk)
                            for child in node.body:
                                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                                    cend = getattr(child, "end_lineno", child.lineno)
                                    args = [a.arg for a in child.args.args]
                                    items.append(
                                        (
                                            child.lineno,
                                            f"    def {child.name}({', '.join(args)})  [L{child.lineno}-{cend}]",
                                        )
                                    )
                        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                            # Top-level functions only (direct children of Module)
                            end = getattr(node, "end_lineno", node.lineno)
                            args = [a.arg for a in node.args.args]
                            items.append(
                                (
                                    node.lineno,
                                    f"  def {node.name}({', '.join(args)})  [L{node.lineno}-{end}]",
                                )
                            )

                    items.sort(key=lambda x: x[0])
                    body = (
                        "\n".join(item for _, item in items)
                        if items
                        else "  (no functions or classes found)"
                    )
                except SyntaxError:
                    body = "  (SyntaxError, falling back to regex)\n"
                    # Fall through to regex
                    for i, line in enumerate(lines, 1):
                        if _re.match(r"\s*(def |async def |class )", line):
                            body += f"  L{i}: {line.strip()}\n"
            else:
                # Regex fallback for JS, TS, YAML, MD, etc.
                patterns = [
                    r"^(export\s+)?(async\s+)?function\s+\w+",
                    r"^(export\s+)?class\s+\w+",
                    r"^(const|let|var)\s+\w+\s*=\s*(async\s+)?\(",
                    r"^#{1,3}\s+",  # Markdown headings
                ]
                regex = _re.compile("|".join(patterns))
                matched: list[str] = []
                for i, line in enumerate(lines, 1):
                    if regex.match(line.strip()):
                        matched.append(f"  L{i}: {line.strip()[:100]}")
                body = "\n".join(matched) if matched else "  (no structure detected)"

            result = f"{path} ({total_lines} lines)\n{body}"
            # Cap output at 4000 chars
            if len(result) > 4000:
                result = result[:3950] + "\n... (output truncated)"
            return result

        return await asyncio.to_thread(_outline)

    async def read_lines(path: str, start: int, end: int) -> str:
        """Read a specific line range from a source file (1-indexed, inclusive).

        Max 200 lines per call. Use get_file_outline first to find line numbers.
        """
        target = (_PROJECT_ROOT / path).resolve()

        if not _is_safe_path(target):
            return f"Blocked: '{path}' is outside the project root."
        if not target.exists():
            return f"Error: File '{path}' does not exist."

        if start < 1:
            return "Error: start line must be >= 1."
        if end < start:
            return "Error: end line must be >= start line."
        if end - start > 199:
            return f"Error: Range too large ({end - start + 1} lines). Max 200 lines per call. Split into smaller ranges."

        def _read() -> str:
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"Error reading file: {e}"

            all_lines = text.splitlines()
            total = len(all_lines)
            actual_end = min(end, total)

            if start > total:
                return f"Error: start={start} exceeds file length ({total} lines)."

            selected = all_lines[start - 1 : actual_end]
            numbered = "\n".join(f"L{start + i}: {line}" for i, line in enumerate(selected))
            header = f"{path} — Lines {start}-{actual_end} (of {total} total):\n"
            return header + numbered

        return await asyncio.to_thread(_read)

    # Register all tools

    registry.register_tool(
        name="get_version_info",
        description="Get current version info: git commit, branch, Python version, and model settings",
        parameters={"type": "object", "properties": {}},
        handler=get_version_info,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="read_source_file",
        description="Read the contents of a source file within the project (path relative to project root)",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root, e.g. 'app/main.py'",
                },
            },
            "required": ["path"],
        },
        handler=read_source_file,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="list_source_files",
        description="List files and directories within the project (optionally filtered by directory)",
        parameters={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path relative to project root (empty string for root)",
                },
            },
        },
        handler=list_source_files,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="get_runtime_config",
        description="Get the current runtime configuration settings (sensitive values are hidden)",
        parameters={"type": "object", "properties": {}},
        handler=get_runtime_config,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="get_system_health",
        description="Check system health: Ollama connectivity, vector search, and data directory status",
        parameters={"type": "object", "properties": {}},
        handler=get_system_health,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="search_source_code",
        description="Search for a pattern in the project source code files (.py and .md)",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (plain text or regex)",
                },
            },
            "required": ["pattern"],
        },
        handler=search_source_code,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="get_skill_details",
        description="Get detailed information about a specific skill: its tools, instructions, and version",
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to inspect",
                },
            },
            "required": ["skill_name"],
        },
        handler=get_skill_details,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="get_recent_logs",
        description="Get the most recent lines from the application log file (data/localforge.log)",
        parameters={
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to retrieve (default: 100, max: 500)",
                    "default": 100,
                },
            },
        },
        handler=get_recent_logs,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="write_source_file",
        description=(
            "Write content to a source file within the project. "
            "Creates the file and any missing parent directories. "
            "Requires AGENT_WRITE_ENABLED=true. "
            "Use for NEW files. For edits to existing files, prefer apply_patch."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root, e.g. 'app/new_module.py'",
                },
                "content": {
                    "type": "string",
                    "description": "Full content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=write_source_file,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="apply_patch",
        description=(
            "Apply a targeted text replacement in an existing source file. "
            "Finds the FIRST occurrence of `search` and replaces it with `replace`. "
            "Safer than write_source_file for small edits. "
            "Requires AGENT_WRITE_ENABLED=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
                "search": {
                    "type": "string",
                    "description": "Exact text to find in the file (must match exactly, including whitespace)",
                },
                "replace": {
                    "type": "string",
                    "description": "Text to replace the found string with",
                },
            },
            "required": ["path", "search", "replace"],
        },
        handler=apply_patch,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="preview_patch",
        description=(
            "Generates a unified diff preview of replacing `search` with `replace` in `path`. "
            "Does NOT modify the file. Returns a markdown diff so you can verify changes first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
                "search": {
                    "type": "string",
                    "description": "Exact text to find in the file (must match exactly, including whitespace)",
                },
                "replace": {
                    "type": "string",
                    "description": "Text to replace the found string with",
                },
            },
            "required": ["path", "search", "replace"],
        },
        handler=preview_patch,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="get_file_outline",
        description=(
            "Get a structural outline of a source file: functions, classes, and line numbers. "
            "Uses AST for Python files, regex fallback for other types. "
            "Does NOT read full file content — use this first on large files (>200 lines), "
            "then use read_lines to read specific sections."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root, e.g. 'app/agent/loop.py'",
                },
            },
            "required": ["path"],
        },
        handler=get_file_outline,
        skill_name="selfcode",
    )

    registry.register_tool(
        name="read_lines",
        description=(
            "Read a specific range of lines from a source file (1-indexed, inclusive). "
            "Max 200 lines per call. Use get_file_outline first to find which lines to read."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
                "start": {
                    "type": "integer",
                    "description": "First line to read (1-indexed)",
                },
                "end": {
                    "type": "integer",
                    "description": "Last line to read (1-indexed, inclusive). Max start+199.",
                },
            },
            "required": ["path", "start", "end"],
        },
        handler=read_lines,
        skill_name="selfcode",
    )
