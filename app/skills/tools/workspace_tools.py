"""workspace_tools.py — Multi-project workspace management + codegen.

Provides tools for creating, switching, and managing project workspaces,
scaffolding from templates, and delivering projects (GitHub push, ZIP).

Requires PROJECTS_ROOT to be set in .env to enable multi-project features.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Module-level engine reference — set during registration
_engine = None


def register(registry: SkillRegistry, projects_root: str = "") -> None:
    """Register workspace and codegen tools."""
    from app.workspace.engine import WorkspaceEngine

    global _engine
    _engine = WorkspaceEngine(projects_root)

    async def list_workspaces() -> str:
        """List all available project workspaces."""
        if not _engine.projects_root:
            return (
                "Error: PROJECTS_ROOT is not configured. "
                "Set PROJECTS_ROOT=/path/to/projects in your .env."
            )
        if not _engine.projects_root.exists():
            return f"Error: projects_root '{_engine.projects_root}' does not exist."

        workspaces = _engine.list_workspaces()
        if not workspaces:
            return f"No projects found in {_engine.projects_root}"

        lines = [f"**Workspaces** (root: `{_engine.projects_root}`):"]
        for ws in workspaces:
            lines.append(f"- `{ws['name']}/`")
        return "\n".join(lines)

    async def switch_workspace(name: str) -> str:
        """Switch the active workspace by name."""
        try:
            path = _engine.set_active("", name)
            return f"Workspace switched to `{name}` at `{path}`"
        except Exception as e:
            return f"Error: {e}"

    async def get_workspace_info() -> str:
        """Get info about the currently active workspace."""
        root = _engine.get_active_root()
        info = await _engine.get_workspace_info(root.name)
        if "error" in info:
            return info["error"]
        lines = [
            f"**Workspace**: `{info['name']}`",
            f"**Path**: `{info['path']}`",
            f"**Branch**: {info.get('branch', 'N/A')}",
            f"**Python files**: {info.get('py_files', 0)}",
        ]
        return "\n".join(lines)

    async def create_workspace(name: str) -> str:
        """Create a new empty project workspace with git init."""
        try:
            path = _engine.create_workspace(name)
            return f"Workspace `{name}` created at `{path}`"
        except Exception as e:
            return f"Error: {e}"

    async def scaffold_project(name: str, template: str, description: str = "") -> str:
        """Create a new workspace from a template (html-static, python-fastapi, react-vite, nextjs)."""
        from app.workspace.templates import scaffold

        try:
            path, files = scaffold(_engine, name, template, description=description)
            file_list = "\n".join(f"  - {f}" for f in files)
            return f"Project `{name}` created from `{template}` at `{path}`\n\nFiles:\n{file_list}"
        except Exception as e:
            return f"Error: {e}"

    async def list_project_templates() -> str:
        """List available project templates for scaffolding."""
        from app.workspace.templates import list_templates

        templates = list_templates()
        lines = ["**Available templates:**"]
        for t in templates:
            files = ", ".join(t["files"][:5])
            if len(t["files"]) > 5:
                files += f", ... ({len(t['files'])} total)"
            lines.append(f"- **{t['name']}**: {t['description']}")
            lines.append(f"  Files: {files}")
        return "\n".join(lines)

    async def deliver_project(method: str, name: str = "") -> str:
        """Deliver a project: method is 'github', 'zip', or 'preview'."""
        import os

        root = _engine.get_active_root()
        ws_name = name or root.name

        if method == "github":
            from app.workspace.delivery import push_to_github

            token = os.getenv("GITHUB_TOKEN", "")
            if not token:
                return "Error: GITHUB_TOKEN not set. Cannot push to GitHub."
            return await push_to_github(root, ws_name, token)

        if method == "zip":
            from app.workspace.delivery import create_zip

            zip_path = await create_zip(root)
            return f"ZIP created at `{zip_path}` ({zip_path.stat().st_size / 1024:.0f} KB)"

        if method == "preview":
            from app.workspace.delivery import serve_preview

            url = await serve_preview(root)
            return f"Preview server started: {url}"

        return f"Error: Unknown delivery method '{method}'. Use 'github', 'zip', or 'preview'."

    # Register all tools
    registry.register_tool(
        name="list_workspaces",
        description="List all available project workspaces in the configured projects_root directory.",
        parameters={"type": "object", "properties": {}},
        handler=list_workspaces,
        skill_name="workspace",
    )
    registry.register_tool(
        name="switch_workspace",
        description="Switch the active project workspace by name.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project directory name."},
            },
            "required": ["name"],
        },
        handler=switch_workspace,
        skill_name="workspace",
    )
    registry.register_tool(
        name="get_workspace_info",
        description="Get info about the currently active workspace: path, git branch, file count.",
        parameters={"type": "object", "properties": {}},
        handler=get_workspace_info,
        skill_name="workspace",
    )
    registry.register_tool(
        name="create_workspace",
        description="Create a new empty project workspace with git init.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name (alphanumeric, hyphens, underscores)."},
            },
            "required": ["name"],
        },
        handler=create_workspace,
        skill_name="workspace",
    )
    registry.register_tool(
        name="scaffold_project",
        description=(
            "Create a new project from a template. "
            "Templates: html-static, python-fastapi, react-vite, nextjs. "
            "Use list_project_templates to see details."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name."},
                "template": {"type": "string", "description": "Template name (e.g. html-static, python-fastapi)."},
                "description": {"type": "string", "description": "Brief project description."},
            },
            "required": ["name", "template"],
        },
        handler=scaffold_project,
        skill_name="workspace",
    )
    registry.register_tool(
        name="list_project_templates",
        description="List available project templates for scaffolding (html-static, python-fastapi, react-vite, nextjs).",
        parameters={"type": "object", "properties": {}},
        handler=list_project_templates,
        skill_name="workspace",
    )
    registry.register_tool(
        name="deliver_project",
        description="Deliver the active project: push to GitHub ('github'), create ZIP ('zip'), or start preview server ('preview').",
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "'github', 'zip', or 'preview'."},
                "name": {"type": "string", "description": "Optional repo/project name override."},
            },
            "required": ["method"],
        },
        handler=deliver_project,
        skill_name="workspace",
    )
