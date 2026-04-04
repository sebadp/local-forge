"""Workspace Engine: project directory lifecycle management.

Manages creation, switching, and path validation for user project
workspaces.  All file/shell tools operate relative to the active
workspace root.

If PROJECTS_ROOT is not configured, the engine falls back to the
LocalForge repository root (backwards compatible).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceEngine:
    """Manages user project workspaces under a shared projects root."""

    def __init__(self, projects_root: str, localforge_root: Path | None = None):
        self._localforge_root = localforge_root or Path(__file__).resolve().parents[2]
        self._projects_root: Path | None = None
        if projects_root:
            self._projects_root = Path(projects_root).expanduser().resolve()
        # phone -> active workspace path
        self._active: dict[str, Path] = {}

    @property
    def projects_root(self) -> Path | None:
        return self._projects_root

    @property
    def localforge_root(self) -> Path:
        return self._localforge_root

    # --- Lifecycle ---

    def create_workspace(self, name: str, phone: str = "") -> Path:
        """Create a new project directory with git init. Returns the path."""
        if not self._projects_root:
            raise RuntimeError("PROJECTS_ROOT is not configured")

        if not self._is_safe_name(name):
            raise ValueError(f"Invalid workspace name: {name!r}")

        workspace = self._projects_root / name
        if workspace.exists():
            raise FileExistsError(f"Workspace '{name}' already exists at {workspace}")

        workspace.mkdir(parents=True)

        # git init (best-effort)
        try:
            subprocess.run(
                ["git", "init"],
                cwd=workspace,
                capture_output=True,
                timeout=10,
            )
        except Exception:
            logger.warning("git init failed for workspace %s", name, exc_info=True)

        if phone:
            self._active[phone] = workspace

        logger.info("workspace.created: %s at %s", name, workspace)
        return workspace

    def set_active(self, phone: str, name: str) -> Path:
        """Set the active workspace for a phone number. Returns the path."""
        if not self._projects_root:
            raise RuntimeError("PROJECTS_ROOT is not configured")

        if not self._is_safe_name(name):
            raise ValueError(f"Invalid workspace name: {name!r}")

        target = (self._projects_root / name).resolve()
        if not target.is_relative_to(self._projects_root):
            raise ValueError("Path traversal detected")
        if not target.is_dir():
            raise FileNotFoundError(f"Workspace '{name}' not found")

        self._active[phone] = target
        self._propagate_root(target)
        logger.info("workspace.active: phone=%s, workspace=%s", phone, name)
        return target

    def get_active_root(self, phone: str = "") -> Path:
        """Return the active workspace root for a phone, or LocalForge root."""
        if phone and phone in self._active:
            return self._active[phone]
        return self._localforge_root

    def list_workspaces(self) -> list[dict]:
        """List all project directories under projects_root."""
        if not self._projects_root or not self._projects_root.exists():
            return []

        result = []
        for d in sorted(self._projects_root.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                result.append({
                    "name": d.name,
                    "path": str(d),
                })
        return result

    def is_valid_path(self, path: Path, phone: str = "") -> bool:
        """Check if a path is within the active workspace or LocalForge root."""
        try:
            resolved = path.resolve()
        except Exception:
            return False

        root = self.get_active_root(phone)
        if resolved.is_relative_to(root):
            return True

        # Also allow paths within localforge root (always safe)
        if resolved.is_relative_to(self._localforge_root):
            return True

        return False

    # --- Internal ---

    @staticmethod
    def _is_safe_name(name: str) -> bool:
        """Validate workspace name: no path traversal, simple directory name."""
        if not name or not name.strip():
            return False
        if "/" in name or "\\" in name or ".." in name:
            return False
        if name.startswith("."):
            return False
        # Only allow alphanumeric, hyphens, underscores
        return all(c.isalnum() or c in "-_" for c in name)

    def _propagate_root(self, target: Path) -> None:
        """Propagate active workspace to selfcode_tools and shell_tools."""
        try:
            import app.skills.tools.selfcode_tools as sc

            if hasattr(sc, "_PROJECT_ROOT"):
                sc._PROJECT_ROOT = target  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            import app.skills.tools.shell_tools as sh

            if hasattr(sh, "_PROJECT_ROOT"):
                sh._PROJECT_ROOT = target  # type: ignore[attr-defined]
        except Exception:
            pass

    async def get_workspace_info(self, name: str) -> dict:
        """Get detailed info about a workspace."""
        if not self._projects_root:
            return {"error": "PROJECTS_ROOT not configured"}

        target = self._projects_root / name
        if not target.is_dir():
            return {"error": f"Workspace '{name}' not found"}

        def _info() -> dict:
            info: dict = {"name": name, "path": str(target)}
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=target, capture_output=True, text=True, timeout=5,
                )
                info["branch"] = result.stdout.strip() if result.returncode == 0 else "N/A"
            except Exception:
                info["branch"] = "N/A"

            try:
                py_count = sum(1 for _ in target.rglob("*.py") if ".git" not in _.parts)
                info["py_files"] = py_count
            except Exception:
                info["py_files"] = 0

            return info

        return await asyncio.to_thread(_info)
