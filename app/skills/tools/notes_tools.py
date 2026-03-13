from __future__ import annotations

from typing import TYPE_CHECKING

from app.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.llm.client import OllamaClient


import logging

logger = logging.getLogger(__name__)


def register(
    registry: SkillRegistry,
    repository: Repository,
    ollama_client: OllamaClient | None = None,
    embed_model: str | None = None,
    vec_available: bool = False,
) -> None:
    async def save_note(title: str, content: str, **_extra: object) -> str:
        logger.info(f"Saving note: {title}")
        note_id = await repository.save_note(title, content)
        msg = f"Note saved (ID: {note_id}): {title}"
        logger.info(msg)

        # Audit log (best-effort)
        try:
            from app.provenance.context import get_audit_logger
            from app.provenance.models import Action, Actor, EntityType

            _al = get_audit_logger()
            if _al:
                await _al.log_mutation(
                    EntityType.NOTE,
                    note_id,
                    Action.CREATE,
                    Actor.TOOL,
                    after_snapshot=f"{title}: {content[:200]}",
                )
        except Exception:
            pass

        # Embed the new note (best-effort)
        if ollama_client and embed_model and vec_available:
            from app.embeddings.indexer import embed_note

            await embed_note(
                note_id,
                f"{title}: {content}",
                repository,
                ollama_client,
                embed_model,
            )

        return msg

    async def list_notes() -> str:
        logger.info("Listing notes")
        notes = await repository.list_notes()
        if not notes:
            logger.info("No notes found")
            return "No notes found."
        lines = []
        for n in notes:
            lines.append(f"[{n.id}] {n.title}: {n.content[:80]}")
        count = len(notes)
        logger.info(f"Found {count} notes")
        return "\n".join(lines)

    async def search_notes(query: str) -> str:
        logger.info(f"Searching notes with query: {query}")

        # Try semantic search first
        if ollama_client and embed_model and vec_available:
            try:
                query_emb = await ollama_client.embed([query], model=embed_model)
                notes = await repository.search_similar_notes(query_emb[0], top_k=5)
                if notes:
                    lines = []
                    for n in notes:
                        lines.append(f"[{n.id}] {n.title}: {n.content[:80]}")
                    logger.info(f"Semantic search found {len(notes)} matching notes")
                    return "\n".join(lines)
            except Exception:
                logger.warning(
                    "Semantic note search failed, falling back to keyword", exc_info=True
                )

        # Fallback to keyword search
        notes = await repository.search_notes(query)
        if not notes:
            logger.info(f"No notes match query: {query}")
            return f"No notes matching '{query}'."
        lines = []
        for n in notes:
            lines.append(f"[{n.id}] {n.title}: {n.content[:80]}")
        logger.info(f"Found {len(notes)} matching notes")
        return "\n".join(lines)

    async def get_note(note_id: int) -> str:
        logger.info(f"Getting full content of note ID: {note_id}")
        note = await repository.get_note(note_id)
        if not note:
            return f"Note {note_id} not found."
        return f"[{note.id}] {note.title}\n\n{note.content}"

    async def delete_note(note_id: int) -> str:
        logger.info(f"Deleting note ID: {note_id}")
        # Fetch before deleting for audit snapshot (best-effort)
        try:
            _note_before = await repository.get_note(note_id)
        except Exception:
            _note_before = None
        deleted = await repository.delete_note(note_id)
        if deleted:
            # Audit log (best-effort)
            try:
                from app.provenance.context import get_audit_logger
                from app.provenance.models import Action, Actor, EntityType

                _al = get_audit_logger()
                if _al:
                    _snap = (
                        f"{_note_before.title}: {_note_before.content[:200]}"
                        if _note_before
                        else ""
                    )
                    await _al.log_mutation(
                        EntityType.NOTE,
                        note_id,
                        Action.DELETE,
                        Actor.TOOL,
                        before_snapshot=_snap,
                    )
            except Exception:
                pass
            # Remove embedding (best-effort)
            if vec_available:
                from app.embeddings.indexer import remove_note_embedding

                await remove_note_embedding(note_id, repository)
            msg = f"Note {note_id} deleted."
            logger.info(msg)
            return msg
        msg = f"Note {note_id} not found."
        logger.warning(msg)
        return msg

    registry.register_tool(
        name="save_note",
        description="Save a new note with a title and content",
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the note",
                },
                "content": {
                    "type": "string",
                    "description": "Content of the note",
                },
            },
            "required": ["title", "content"],
        },
        handler=save_note,
        skill_name="notes",
    )

    registry.register_tool(
        name="list_notes",
        description=(
            "List all saved notes with their IDs and a short preview (first 80 chars). "
            "Use get_note(note_id) to read the full content of a specific note."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=list_notes,
        skill_name="notes",
    )

    registry.register_tool(
        name="search_notes",
        description=(
            "Search notes by keyword in title or content. "
            "Returns a short preview (first 80 chars). "
            "Use get_note(note_id) to read the full content of a specific note."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword",
                },
            },
            "required": ["query"],
        },
        handler=search_notes,
        skill_name="notes",
    )

    registry.register_tool(
        name="get_note",
        description="Get the full content of a specific note by its ID. Use this when the user asks to see or read a complete note.",
        parameters={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the note to retrieve",
                },
            },
            "required": ["note_id"],
        },
        handler=get_note,
        skill_name="notes",
    )

    registry.register_tool(
        name="delete_note",
        description="Delete a note by its ID",
        parameters={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the note to delete",
                },
            },
            "required": ["note_id"],
        },
        handler=delete_note,
        skill_name="notes",
    )
