"""Delivery pipeline: GitHub push, ZIP creation, preview server.

Best-effort: errors are logged and returned as strings, never raised.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories excluded from ZIP archives
_ZIP_EXCLUDES = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".next", "dist"}


async def push_to_github(
    workspace: Path,
    repo_name: str,
    github_token: str,
    private: bool = True,
) -> str:
    """Git add + commit + create repo + push. Returns the repo URL or error."""

    def _do_push() -> str:
        try:
            # Stage all files
            subprocess.run(
                ["git", "add", "."],
                cwd=workspace, capture_output=True, timeout=30,
            )
            # Commit
            subprocess.run(
                ["git", "commit", "-m", "Initial commit from LocalForge"],
                cwd=workspace, capture_output=True, timeout=30,
            )
            # Create repo with gh CLI
            visibility = "--private" if private else "--public"
            result = subprocess.run(
                ["gh", "repo", "create", repo_name, visibility, "--source=.", "--push"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
                env={
                    **__import__("os").environ,
                    "GH_TOKEN": github_token,
                },
            )
            if result.returncode == 0:
                # Extract URL from output
                for line in result.stdout.strip().splitlines():
                    if "github.com" in line:
                        return line.strip()
                return f"https://github.com/{repo_name}"
            return f"Error: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return "Error: GitHub push timed out"
        except FileNotFoundError:
            return "Error: 'gh' CLI not found. Install with: brew install gh"
        except Exception as e:
            return f"Error: {e}"

    return await asyncio.to_thread(_do_push)


async def create_zip(workspace: Path) -> Path:
    """Create a ZIP archive of the workspace, excluding heavy directories.

    Returns the path to the created .zip file.
    """

    def _do_zip() -> Path:
        # Use shutil but we need to exclude certain dirs
        # Create a filtered copy first
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_copy = Path(tmp) / workspace.name
            shutil.copytree(
                workspace,
                tmp_copy,
                ignore=shutil.ignore_patterns(*_ZIP_EXCLUDES),
            )
            archive = shutil.make_archive(
                str(workspace.parent / workspace.name),
                "zip",
                root_dir=tmp,
                base_dir=workspace.name,
            )
            return Path(archive)

    return await asyncio.to_thread(_do_zip)


async def serve_preview(workspace: Path, port: int = 0) -> str:
    """Start a temporary HTTP server for static sites.

    Returns the URL. The server runs in background and must be stopped manually.
    """
    import http.server
    import socketserver
    import threading

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(workspace), **kwargs)

        def log_message(self, format, *args):
            pass  # Suppress logs

    def _serve() -> int:
        with socketserver.TCPServer(("", port), Handler) as httpd:
            actual_port = httpd.server_address[1]
            logger.info("preview.started: %s on port %d", workspace.name, actual_port)
            # Store reference so it can be stopped
            _active_servers[str(workspace)] = httpd
            httpd.serve_forever()
            return actual_port

    # Start in background thread
    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    # Wait briefly for the server to start and get port
    await asyncio.sleep(0.2)
    server = _active_servers.get(str(workspace))
    if server:
        actual_port = server.server_address[1]
        return f"http://localhost:{actual_port}"
    return "Error: preview server failed to start"


# Track active preview servers for cleanup
_active_servers: dict[str, object] = {}


def stop_preview(workspace_path: str) -> None:
    """Stop a running preview server."""
    server = _active_servers.pop(workspace_path, None)
    if server and hasattr(server, "shutdown"):
        server.shutdown()  # type: ignore[attr-defined]
