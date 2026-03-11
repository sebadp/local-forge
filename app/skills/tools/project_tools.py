from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from app.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.llm.client import OllamaClient
    from app.memory.daily_log import DailyLog

logger = logging.getLogger(__name__)

_current_user_phone: ContextVar[str | None] = ContextVar("_current_user_phone", default=None)


def set_current_user(phone_number: str) -> None:
    _current_user_phone.set(phone_number)


def register(
    registry: SkillRegistry,
    repository: Repository,
    daily_log: DailyLog | None = None,
    ollama_client: OllamaClient | None = None,
    embed_model: str | None = None,
    vec_available: bool = False,
) -> None:
    async def _resolve_project(name: str) -> tuple[int, str] | str:
        """Resolve project name to (id, name). Returns error string if not found."""
        phone = _current_user_phone.get()
        if not phone:
            return "No user context available."
        project = await repository.get_project_by_name(phone, name)
        if not project:
            return f"Project '{name}' not found. Use list_projects to see active projects."
        return (project.id, project.name)

    async def create_project(name: str, description: str = "") -> str:
        phone = _current_user_phone.get()
        if not phone:
            return "No user context available."
        existing = await repository.get_project_by_name(phone, name)
        if existing:
            return f"Project '{name}' already exists (status: {existing.status})."
        project_id = await repository.create_project(phone, name, description)
        await repository.log_project_activity(project_id, "created", description)
        logger.info("Created project '%s' (id=%d) for %s", name, project_id, phone)
        return f"Project '{name}' created (ID: {project_id})."

    async def list_projects(status: str = "active") -> str:
        phone = _current_user_phone.get()
        if not phone:
            return "No user context available."
        valid_statuses = {"active", "archived", "completed", "all"}
        if status not in valid_statuses:
            status = "active"
        projects = await repository.list_projects(phone, status if status != "all" else None)
        if not projects:
            return f"No {status} projects found."
        lines = []
        for p in projects:
            progress = await repository.get_project_progress(p.id)
            total = progress["total"]
            done = progress["done"]
            pct = int(done / total * 100) if total > 0 else 0
            desc = f" — {p.description}" if p.description else ""
            lines.append(f"[{p.id}] {p.name} ({p.status}){desc} | {done}/{total} tasks ({pct}%)")
        return "\n".join(lines)

    async def get_project(project_name: str) -> str:
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, _ = result
        project = await repository.get_project(project_id)
        if not project:
            return f"Project '{project_name}' not found."
        progress = await repository.get_project_progress(project_id)
        tasks = await repository.list_project_tasks(project_id)
        activity = await repository.get_project_activity(project_id, limit=5)

        total = progress["total"]
        done = progress["done"]
        in_prog = progress["in_progress"]
        pending = progress["pending"]
        pct = int(done / total * 100) if total > 0 else 0

        # Visual progress bar
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines = [
            f"*{project.name}* (ID: {project.id}, status: {project.status})",
            f"{project.description}" if project.description else "",
            f"[{bar}] {pct}%",
            f"Done: {done} | In Progress: {in_prog} | Pending: {pending}",
        ]
        if tasks:
            lines.append("\nTasks:")
            for t in tasks:
                due = f" (due: {t.due_date})" if t.due_date else ""
                lines.append(f"  [{t.id}] [{t.status}] [{t.priority}] {t.title}{due}")
        if activity:
            lines.append("\nRecent activity:")
            for action, detail, ts in activity:
                detail_str = f": {detail}" if detail else ""
                lines.append(f"  {ts[:10]} — {action}{detail_str}")

        return "\n".join(line for line in lines if line)

    async def add_task(
        project_name: str,
        title: str,
        description: str = "",
        priority: str = "medium",
    ) -> str:
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, pname = result
        if priority not in ("low", "medium", "high"):
            priority = "medium"
        task_id = await repository.add_project_task(project_id, title, description, priority)
        await repository.log_project_activity(
            project_id, "task_added", f"[{task_id}] {title} (priority: {priority})"
        )
        logger.info("Added task '%s' (id=%d) to project '%s'", title, task_id, pname)
        return f"Task added to '{pname}': [{task_id}] {title} (priority: {priority})"

    async def update_task(task_id: int, status: str) -> str:
        valid = {"pending", "in_progress", "done"}
        if status not in valid:
            return f"Invalid status '{status}'. Use: pending, in_progress, done."
        task = await repository.get_project_task(task_id)
        if not task:
            return f"Task {task_id} not found."
        updated = await repository.update_task_status(task_id, status)
        if not updated:
            return f"Failed to update task {task_id}."
        project = await repository.get_project(task.project_id)
        pname = project.name if project else str(task.project_id)
        await repository.log_project_activity(
            task.project_id, f"task_{status}", f"[{task_id}] {task.title}"
        )
        if status == "done" and daily_log:
            await daily_log.append(f"Completed task '{task.title}' in project '{pname}'")
        # Check if all tasks done → suggest completing project
        progress = await repository.get_project_progress(task.project_id)
        suffix = ""
        if status == "done" and progress["total"] > 0 and progress["done"] == progress["total"]:
            suffix = (
                "\n\nAll tasks done! Consider completing the project with update_project_status."
            )
        logger.info("Updated task %d to '%s' in project '%s'", task_id, status, pname)
        return f"Task [{task_id}] '{task.title}' → {status} in project '{pname}'.{suffix}"

    async def delete_task(task_id: int) -> str:
        task = await repository.get_project_task(task_id)
        if not task:
            return f"Task {task_id} not found."
        deleted = await repository.delete_project_task(task_id)
        if deleted:
            await repository.log_project_activity(
                task.project_id, "task_deleted", f"[{task_id}] {task.title}"
            )
            logger.info("Deleted task %d", task_id)
            return f"Task [{task_id}] '{task.title}' deleted."
        return f"Failed to delete task {task_id}."

    async def project_progress(project_name: str) -> str:
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, _ = result
        project = await repository.get_project(project_id)
        if not project:
            return f"Project '{project_name}' not found."
        progress = await repository.get_project_progress(project_id)
        total = progress["total"]
        done = progress["done"]
        in_prog = progress["in_progress"]
        pending = progress["pending"]
        pct = int(done / total * 100) if total > 0 else 0

        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines = [
            f"*{project.name}* progress:",
            f"[{bar}] {pct}%",
            f"Done: {done} | In Progress: {in_prog} | Pending: {pending} | Total: {total}",
        ]

        # High-priority pending tasks
        tasks = await repository.list_project_tasks(project_id)
        high_priority = [t for t in tasks if t.priority == "high" and t.status != "done"]
        if high_priority:
            lines.append("\nHigh-priority tasks:")
            for t in high_priority[:5]:
                due = f" (due: {t.due_date})" if t.due_date else ""
                lines.append(f"  [{t.id}] [{t.status}] {t.title}{due}")

        activity = await repository.get_project_activity(project_id, limit=5)
        if activity:
            lines.append("\nRecent activity:")
            for action, detail, ts in activity:
                detail_str = f": {detail}" if detail else ""
                lines.append(f"  {ts[:10]} — {action}{detail_str}")

        return "\n".join(lines)

    async def update_project_status(project_name: str, status: str) -> str:
        valid = {"active", "archived", "completed"}
        if status not in valid:
            return f"Invalid status '{status}'. Use: active, archived, completed."
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, pname = result
        # Log summary before archiving/completing
        if status in ("archived", "completed"):
            progress = await repository.get_project_progress(project_id)
            total = progress["total"]
            done = progress["done"]
            pct = int(done / total * 100) if total > 0 else 0
            await repository.log_project_activity(
                project_id, status, f"Final: {done}/{total} tasks ({pct}%)"
            )
        updated = await repository.update_project_status(project_id, status)
        if updated:
            logger.info("Project '%s' status → '%s'", pname, status)
            return f"Project '{pname}' is now {status}."
        return f"Failed to update project '{pname}'."

    async def add_project_note(project_name: str, content: str, title: str = "") -> str:
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, pname = result
        note_title = title.strip() or None
        note_id = await repository.add_project_note(project_id, content, title=note_title)
        activity_detail = f"{note_title}: {content[:60]}" if note_title else content[:80]
        await repository.log_project_activity(project_id, "note_added", activity_detail)
        # Embed best-effort
        if ollama_client and embed_model and vec_available:
            from app.embeddings.indexer import embed_project_note

            embed_text = f"{note_title}: {content}" if note_title else content
            await embed_project_note(note_id, embed_text, repository, ollama_client, embed_model)
        logger.info("Added note %d to project '%s'", note_id, pname)
        label = f" '{note_title}'" if note_title else ""
        return f"Note{label} saved to project '{pname}' (ID: {note_id})."

    async def get_project_note(note_id: int) -> str:
        """Get the full content of a project note by ID — no truncation."""
        note = await repository.get_project_note(note_id)
        if note is None:
            return f"Note {note_id} not found."
        header = f"[{note.id}]"
        if note.title:
            header += f" {note.title}"
        header += f"  (created: {note.created_at})"
        return f"{header}\n\n{note.content}"

    async def list_project_notes(project_name: str) -> str:
        """List all notes in a project with title and content preview."""
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, pname = result
        notes = await repository.list_project_notes(project_id)
        if not notes:
            return f"No notes in project '{pname}'."
        lines = [f"Notes in '{pname}':"]
        for n in notes:
            header = f"  [{n.id}]"
            if n.title:
                header += f" {n.title}"
            preview = n.content[:300].replace("\n", " ")
            lines.append(f"{header}: {preview}")
        return "\n".join(lines)

    async def search_project_notes(project_name: str, query: str) -> str:
        result = await _resolve_project(project_name)
        if isinstance(result, str):
            return result
        project_id, pname = result
        # Try semantic search first
        if ollama_client and embed_model and vec_available:
            try:
                query_emb = await ollama_client.embed([query], model=embed_model)
                notes = await repository.search_similar_project_notes(
                    project_id, query_emb[0], top_k=5
                )
                if notes:
                    lines = [f"Notes in '{pname}' matching '{query}':"]
                    for n in notes:
                        header = f"  [{n.id}]"
                        if n.title:
                            header += f" {n.title}"
                        lines.append(f"{header}: {n.content[:500]}")
                    return "\n".join(lines)
            except Exception:
                logger.warning("Semantic project note search failed, falling back", exc_info=True)
        # Fallback: filter by query substring (case-insensitive)
        notes = await repository.list_project_notes(project_id)
        if not notes:
            return f"No notes in project '{pname}'."
        query_lower = query.lower()
        matched = [
            n
            for n in notes
            if query_lower in n.content.lower() or (n.title and query_lower in n.title.lower())
        ]
        if not matched:
            return f"No notes in project '{pname}' matching '{query}'."
        lines = [f"Notes in '{pname}' matching '{query}':"]
        for n in matched:
            header = f"  [{n.id}]"
            if n.title:
                header += f" {n.title}"
            lines.append(f"{header}: {n.content[:500]}")
        return "\n".join(lines)

    # --- Register tools ---

    registry.register_tool(
        name="create_project",
        description="Create a new project with a name and optional description",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name (unique per user)"},
                "description": {
                    "type": "string",
                    "description": "Brief description of the project goal",
                },
            },
            "required": ["name"],
        },
        handler=create_project,
        skill_name="projects",
    )

    registry.register_tool(
        name="list_projects",
        description="List projects, optionally filtered by status (active/archived/completed/all)",
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: active (default), archived, completed, all",
                    "enum": ["active", "archived", "completed", "all"],
                },
            },
        },
        handler=list_projects,
        skill_name="projects",
    )

    registry.register_tool(
        name="get_project",
        description="Get full details of a project including tasks and recent activity",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
            },
            "required": ["project_name"],
        },
        handler=get_project,
        skill_name="projects",
    )

    registry.register_tool(
        name="add_task",
        description="Add a task to a project",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task description (optional)"},
                "priority": {
                    "type": "string",
                    "description": "Priority: low, medium (default), high",
                    "enum": ["low", "medium", "high"],
                },
            },
            "required": ["project_name", "title"],
        },
        handler=add_task,
        skill_name="projects",
    )

    registry.register_tool(
        name="update_task",
        description="Update the status of a task (pending, in_progress, done)",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Task ID"},
                "status": {
                    "type": "string",
                    "description": "New status: pending, in_progress, done",
                    "enum": ["pending", "in_progress", "done"],
                },
            },
            "required": ["task_id", "status"],
        },
        handler=update_task,
        skill_name="projects",
    )

    registry.register_tool(
        name="delete_task",
        description="Delete a task from a project by task ID",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Task ID to delete"},
            },
            "required": ["task_id"],
        },
        handler=delete_task,
        skill_name="projects",
    )

    registry.register_tool(
        name="project_progress",
        description="Get a visual progress report for a project, including high-priority tasks and recent activity",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
            },
            "required": ["project_name"],
        },
        handler=project_progress,
        skill_name="projects",
    )

    registry.register_tool(
        name="update_project_status",
        description="Change project status to active, archived, or completed",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
                "status": {
                    "type": "string",
                    "description": "New status: active, archived, completed",
                    "enum": ["active", "archived", "completed"],
                },
            },
            "required": ["project_name", "status"],
        },
        handler=update_project_status,
        skill_name="projects",
    )

    registry.register_tool(
        name="add_project_note",
        description="Save a note or document to a project (searchable). Use title to name documents like chapters, outlines, drafts.",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
                "content": {
                    "type": "string",
                    "description": "Note or document content (no length limit)",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title for the document (e.g. 'Prologue', 'Chapter 1')",
                },
            },
            "required": ["project_name", "content"],
        },
        handler=add_project_note,
        skill_name="projects",
    )

    registry.register_tool(
        name="get_project_note",
        description="Get the full content of a project note or document by its ID",
        parameters={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "Note ID (from list_project_notes or search_project_notes)",
                },
            },
            "required": ["note_id"],
        },
        handler=get_project_note,
        skill_name="projects",
    )

    registry.register_tool(
        name="list_project_notes",
        description="List all notes and documents in a project with title and content preview",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
            },
            "required": ["project_name"],
        },
        handler=list_project_notes,
        skill_name="projects",
    )

    registry.register_tool(
        name="search_project_notes",
        description="Search notes and documents within a project by semantic or keyword query",
        parameters={
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project"},
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["project_name", "query"],
        },
        handler=search_project_notes,
        skill_name="projects",
    )
