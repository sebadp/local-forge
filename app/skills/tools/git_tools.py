"""Git tools for agentic sessions.

Provides a set of tools for interacting with the local Git repository:
- git_status: Show the current working tree status
- git_diff: Show file-level diff stats
- git_create_branch: Create and switch to a new branch
- git_commit: Stage all changes and commit
- git_push: Push the current branch to origin
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import Settings
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GIT_TIMEOUT = 30  # seconds


def _run_git(*args: str) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Git command timed out."
    except FileNotFoundError:
        return -1, "", "git executable not found."
    except Exception as e:
        return -1, "", str(e)


def register(registry: SkillRegistry, settings: Settings | None = None) -> None:
    """Register all git tools in the skill registry."""

    async def git_status() -> str:
        """Show the current Git status of the project (short format)."""
        code, out, err = await asyncio.to_thread(_run_git, "status", "--short", "--branch")
        if code != 0:
            return f"Error: {err}"
        return out or "(working tree clean)"

    async def git_diff() -> str:
        """Show a summary of staged and unstaged changes (--stat format)."""
        code, out, err = await asyncio.to_thread(_run_git, "diff", "--stat")
        staged_code, staged_out, staged_err = await asyncio.to_thread(
            _run_git, "diff", "--cached", "--stat"
        )
        parts = []
        if code != 0:
            parts.append(f"Error reading unstaged diff: {err}")
        elif out:
            parts.append(f"Unstaged:\n{out}")
        if staged_code != 0:
            parts.append(f"Error reading staged diff: {staged_err}")
        elif staged_out:
            parts.append(f"Staged:\n{staged_out}")
        if not parts:
            return "(no changes)"
        return "\n".join(parts)

    async def git_create_branch(branch_name: str) -> str:
        """Create and switch to a new Git branch."""
        clean = branch_name.strip()
        if not clean or " " in clean:
            return "Error: branch name cannot be empty or contain spaces."
        if clean.startswith("-"):
            return f"Error: invalid branch name '{clean}' (cannot start with '-')."
        # Note: no `--` here — `git checkout -b` interprets `--` as the branch name
        # itself. Git already rejects names starting with `-`, so no injection is possible.
        code, out, err = await asyncio.to_thread(_run_git, "checkout", "-b", clean)
        if code != 0:
            return f"Error creating branch '{clean}': {err}"
        return f"✅ Created and switched to branch: {clean}"

    async def git_commit(message: str) -> str:
        """Stage ALL changes (git add -A) and create a commit with the given message."""
        if not message.strip():
            return "Error: commit message cannot be empty."
        # Stage — check for errors before committing
        add_code, _, add_err = await asyncio.to_thread(_run_git, "add", "-A")
        if add_code != 0:
            return f"Error staging changes (git add -A): {add_err}"
        # Commit
        code, out, err = await asyncio.to_thread(_run_git, "commit", "-m", message.strip())
        if code != 0:
            if "nothing to commit" in err or "nothing added" in err:
                return "Nothing to commit — working tree is clean."
            return f"Error committing: {err}"
        return f"✅ Committed: {message.strip()}\n{out}"

    async def git_push(branch_name: str = "") -> str:
        """Push the current branch (or the specified branch) to origin."""
        cmd: list[str]
        if branch_name:
            clean = branch_name.strip()
            # Block flag injection: names starting with `-` would be interpreted as git flags
            if clean.startswith("-"):
                return f"Error: invalid branch name '{clean}' (cannot start with '-')."
            cmd = ["push", "--set-upstream", "origin", "--", clean]
        else:
            cmd = ["push"]
        code, out, err = await asyncio.to_thread(_run_git, *cmd)
        if code != 0:
            return f"Error pushing: {err}"
        return f"✅ Pushed successfully.\n{out or err}"

    async def git_create_pr(
        title: str, body: str, head_branch: str, base_branch: str = "main"
    ) -> str:
        """Create a Pull Request on GitHub using the REST API."""
        if not settings or not settings.github_token or not settings.github_repo:
            return "Error: GITHUB_TOKEN or GITHUB_REPO not configured in .env"

        url = f"https://api.github.com/repos/{settings.github_repo}/pulls"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {settings.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }

        async with httpx.AsyncClient() as client:
            try:
                # Use a short timeout because APIs can hang
                response = await client.post(url, headers=headers, json=data, timeout=15.0)
                if response.status_code == 201:
                    pr_data = response.json()
                    return f"✅ Pull Request created successfully!\nURL: {pr_data.get('html_url')}"
                else:
                    return f"Error creating PR (HTTP {response.status_code}): {response.text}"
            except Exception as e:
                return f"Error connecting to GitHub API: {e}"

    registry.register_tool(
        name="git_status",
        description="Show the current Git status of the project (short format with branch info)",
        parameters={"type": "object", "properties": {}},
        handler=git_status,
        skill_name="git",
    )

    registry.register_tool(
        name="git_diff",
        description="Show a summary of staged and unstaged file changes (--stat format)",
        parameters={"type": "object", "properties": {}},
        handler=git_diff,
        skill_name="git",
    )

    registry.register_tool(
        name="git_create_branch",
        description="Create and switch to a new Git branch",
        parameters={
            "type": "object",
            "properties": {
                "branch_name": {
                    "type": "string",
                    "description": "Name for the new branch, e.g. 'fix/header-color'",
                },
            },
            "required": ["branch_name"],
        },
        handler=git_create_branch,
        skill_name="git",
    )

    registry.register_tool(
        name="git_commit",
        description=(
            "Stage ALL current changes (git add -A) and create a commit. "
            "Use a conventional commit message, e.g. 'fix: correct header color'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message (conventional commit format recommended)",
                },
            },
            "required": ["message"],
        },
        handler=git_commit,
        skill_name="git",
    )

    registry.register_tool(
        name="git_push",
        description="Push the current or specified branch to the 'origin' remote",
        parameters={
            "type": "object",
            "properties": {
                "branch_name": {
                    "type": "string",
                    "description": "Branch to push. If empty, pushes the current branch.",
                },
            },
        },
        handler=git_push,
        skill_name="git",
    )

    registry.register_tool(
        name="git_create_pr",
        description=(
            "Create a Pull Request on GitHub for the specified branch. "
            "Requires GITHUB_TOKEN and GITHUB_REPO in the environment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title of the pull request",
                },
                "body": {
                    "type": "string",
                    "description": "The contents of the pull request body (markdown supported)",
                },
                "head_branch": {
                    "type": "string",
                    "description": "The name of the branch where your changes are implemented",
                },
                "base_branch": {
                    "type": "string",
                    "description": "The name of the branch you want the changes pulled into. Default is 'main'.",
                    "default": "main",
                },
            },
            "required": ["title", "body", "head_branch"],
        },
        handler=git_create_pr,
        skill_name="git",
    )

    # --- Undo / rollback tools (Plan 58) ---

    async def git_undo(scope: str = "file", file_path: str = "") -> str:
        """Undo changes: restore a file or revert the last commit."""
        if scope == "file":
            if not file_path.strip():
                return "Error: file_path is required when scope='file'."
            clean = file_path.strip()
            if clean.startswith("-"):
                return f"Error: invalid file path '{clean}'."
            code, out, err = await asyncio.to_thread(_run_git, "checkout", "--", clean)
            if code != 0:
                return f"Error restoring '{clean}': {err}"
            return f"✅ Restored '{clean}' to last committed version."
        elif scope == "commit":
            code, out, err = await asyncio.to_thread(
                _run_git, "revert", "HEAD", "--no-edit"
            )
            if code != 0:
                return f"Error reverting last commit: {err}"
            return f"✅ Reverted last commit.\n{out}"
        else:
            return f"Error: unknown scope '{scope}'. Use 'file' or 'commit'."

    async def git_stash(action: str = "save", message: str = "") -> str:
        """Manage the git stash: save, pop, or list stashed changes."""
        if action == "save":
            args: list[str] = ["stash", "push"]
            if message.strip():
                args += ["-m", message.strip()]
            code, out, err = await asyncio.to_thread(_run_git, *args)
            if code != 0:
                return f"Error stashing: {err}"
            return out or "✅ Changes stashed."
        elif action == "pop":
            code, out, err = await asyncio.to_thread(_run_git, "stash", "pop")
            if code != 0:
                return f"Error popping stash: {err}"
            return out or "✅ Stash applied and removed."
        elif action == "list":
            code, out, err = await asyncio.to_thread(_run_git, "stash", "list")
            if code != 0:
                return f"Error listing stash: {err}"
            return out or "(stash is empty)"
        else:
            return f"Error: unknown action '{action}'. Use 'save', 'pop', or 'list'."

    registry.register_tool(
        name="git_undo",
        description=(
            "Undo changes: restore a single file to its last committed state "
            "(scope='file') or revert the last commit (scope='commit')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["file", "commit"],
                    "description": "'file' to restore a file, 'commit' to revert last commit",
                },
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to restore (required when scope='file')",
                },
            },
            "required": ["scope"],
        },
        handler=git_undo,
        skill_name="git",
    )

    registry.register_tool(
        name="git_stash",
        description="Manage the git stash: save current changes, pop the latest stash, or list all stashes.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["save", "pop", "list"],
                    "description": "'save' to stash changes, 'pop' to restore, 'list' to show stashes",
                },
                "message": {
                    "type": "string",
                    "description": "Optional message for the stash (only used with action='save')",
                },
            },
            "required": ["action"],
        },
        handler=git_stash,
        skill_name="git",
    )
