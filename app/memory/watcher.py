"""Bidirectional MEMORY.md watcher: file edits sync to SQLite."""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.memory.markdown import MemoryFile

logger = logging.getLogger(__name__)

MEMORY_LINE_RE = re.compile(r"^-\s+(?:\[([^\]]*)\]\s+)?(.+)$")


def parse_memory_file(content: str) -> list[tuple[str, str | None]]:
    """Parse MEMORY.md content into list of (content, category) tuples."""
    results = []
    for line in content.splitlines():
        line = line.strip()
        m = MEMORY_LINE_RE.match(line)
        if m:
            category = m.group(1) or None
            memory_content = m.group(2).strip()
            if memory_content:
                results.append((memory_content, category))
    return results


class _MemoryFileHandler(FileSystemEventHandler):
    """watchdog handler that triggers sync on MEMORY.md changes."""

    def __init__(self, watcher: MemoryWatcher):
        self._watcher = watcher

    def _is_target(self, event: FileSystemEvent) -> bool:
        if event.is_directory:
            return False

        # event.src_path can be bytes or str, handle safely
        src_path = os.fsdecode(event.src_path)
        target_name = Path(self._watcher._memory_file._path).name
        return Path(src_path).name == target_name

    def on_modified(self, event: FileSystemEvent) -> None:
        if self._is_target(event):
            self._watcher._on_file_changed()

    def on_created(self, event: FileSystemEvent) -> None:
        # Editors with atomic rename create a new file
        if self._is_target(event):
            self._watcher._on_file_changed()


class MemoryWatcher:
    """Watches MEMORY.md for external edits and syncs changes to SQLite.

    Uses a sync guard to prevent feedback loops:
    - When MemoryFile.sync() writes the file, the guard is set
    - When watchdog detects the change, it checks the guard and skips if set
    """

    def __init__(
        self,
        memory_file: MemoryFile,
        repository: Repository,
        loop,
    ):
        self._memory_file = memory_file
        self._repository = repository
        self._loop = loop
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._syncing = threading.Event()

    def start(self) -> None:
        """Start watching MEMORY.md for changes."""
        path = Path(self._memory_file._path)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        handler = _MemoryFileHandler(self)
        self._observer = Observer()
        self._observer.daemon = True
        self._observer.schedule(handler, str(path.parent), recursive=False)
        self._observer.start()
        logger.info("Memory watcher started for %s", path)

    def stop(self) -> None:
        """Stop watching."""
        if self._observer:
            self._observer.stop()  # type: ignore[attr-defined]
            self._observer.join(timeout=5)  # type: ignore[attr-defined]
            logger.info("Memory watcher stopped")

    def set_sync_guard(self) -> None:
        """Set the guard to prevent re-entrant sync."""
        self._syncing.set()

    def clear_sync_guard(self) -> None:
        """Clear the guard after sync completes."""
        self._syncing.clear()

    def _on_file_changed(self) -> None:
        """Called from watchdog thread when file changes."""
        if self._syncing.is_set():
            logger.debug("Skipping sync (guard set)")
            return

        import asyncio

        try:
            asyncio.run_coroutine_threadsafe(
                self._sync_from_file(),
                self._loop,
            )
        except Exception:
            logger.warning("Failed to schedule file sync", exc_info=True)

    async def _sync_from_file(self) -> None:
        """Read MEMORY.md and sync changes to SQLite."""
        path = Path(self._memory_file._path)
        if not path.exists():
            return

        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read memory file", exc_info=True)
            return

        file_memories = parse_memory_file(content)
        all_db_memories = await self._repository.list_memories()
        # Exclude self_correction from sync so it stays in DB but not in user's markdown
        db_memories = [m for m in all_db_memories if m.category != "self_correction"]

        # Build sets for comparison
        file_set = {(c, cat) for c, cat in file_memories}
        db_set = {(m.content, m.category) for m in db_memories}

        # Memories in file but not in DB → add
        to_add = file_set - db_set
        # Memories in DB but not in file → deactivate
        to_remove = db_set - file_set

        changed = False
        for content_text, category in to_add:
            mem_id = await self._repository.add_memory(content_text, category)
            logger.info("Synced from file → added: %s", content_text[:80])
            # Audit log (best-effort)
            try:
                from app.provenance.context import get_audit_logger
                from app.provenance.models import Action, Actor, EntityType

                _al = get_audit_logger()
                if _al:
                    await _al.log_mutation(
                        EntityType.MEMORY,
                        mem_id,
                        Action.CREATE,
                        Actor.FILE_SYNC,
                        after_snapshot=content_text,
                    )
                    await _al.version_memory(mem_id, content_text, Actor.FILE_SYNC)
            except Exception:
                pass
            changed = True

        for content_text, _category in to_remove:
            # Get memory ID before removal for audit
            _rem_id = await self._repository.remove_memory_return_id(content_text)
            if _rem_id is None:
                await self._repository.remove_memory(content_text)
            logger.info("Synced from file → removed: %s", content_text[:80])
            # Audit log (best-effort)
            if _rem_id is not None:
                try:
                    from app.provenance.context import get_audit_logger
                    from app.provenance.models import Action, Actor, EntityType

                    _al = get_audit_logger()
                    if _al:
                        await _al.log_mutation(
                            EntityType.MEMORY,
                            _rem_id,
                            Action.DELETE,
                            Actor.FILE_SYNC,
                            before_snapshot=content_text,
                        )
                except Exception:
                    pass
            changed = True

        # Re-sync file to normalize format
        if changed:
            self.set_sync_guard()
            try:
                updated = await self._repository.list_memories()
                await self._memory_file.sync(updated)
            finally:
                # Clear guard after a short delay to let watchdog event pass
                import asyncio

                await asyncio.sleep(0.5)
                self.clear_sync_guard()

            logger.info("Memory sync complete: +%d -%d", len(to_add), len(to_remove))
