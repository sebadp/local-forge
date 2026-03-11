"""High-level embedding operations for memories and notes."""

from __future__ import annotations

import logging

from app.database.repository import Repository
from app.llm.client import OllamaClient

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
# nomic-embed-text context is 8192 tokens. With mixed languages and short tokens,
# the chars/token ratio can drop well below 4. Use a conservative limit.
_MAX_EMBED_CHARS = 6_000


async def embed_memory(
    memory_id: int,
    content: str,
    repository: Repository,
    ollama_client: OllamaClient,
    model: str,
) -> None:
    """Compute and store embedding for a single memory. Best-effort."""
    try:
        embeddings = await ollama_client.embed([content[:_MAX_EMBED_CHARS]], model=model)
        await repository.save_embedding(memory_id, embeddings[0])
    except Exception:
        logger.warning("Failed to embed memory %d", memory_id, exc_info=True)


async def remove_memory_embedding(
    memory_id: int,
    repository: Repository,
) -> None:
    """Remove embedding for a memory. Best-effort."""
    try:
        await repository.delete_embedding(memory_id)
    except Exception:
        logger.warning("Failed to delete embedding for memory %d", memory_id, exc_info=True)


async def embed_note(
    note_id: int,
    text: str,
    repository: Repository,
    ollama_client: OllamaClient,
    model: str,
) -> None:
    """Compute and store embedding for a single note. Best-effort."""
    try:
        embeddings = await ollama_client.embed([text[:_MAX_EMBED_CHARS]], model=model)
        await repository.save_note_embedding(note_id, embeddings[0])
    except Exception:
        logger.warning("Failed to embed note %d", note_id, exc_info=True)


async def remove_note_embedding(
    note_id: int,
    repository: Repository,
) -> None:
    """Remove embedding for a note. Best-effort."""
    try:
        await repository.delete_note_embedding(note_id)
    except Exception:
        logger.warning("Failed to delete embedding for note %d", note_id, exc_info=True)


async def embed_project_note(
    note_id: int,
    content: str,
    repository: Repository,
    ollama_client: OllamaClient,
    model: str,
) -> None:
    """Compute and store embedding for a project note. Best-effort."""
    try:
        embeddings = await ollama_client.embed([content[:_MAX_EMBED_CHARS]], model=model)
        await repository.save_project_note_embedding(note_id, embeddings[0])
    except Exception:
        logger.warning("Failed to embed project note %d", note_id, exc_info=True)


async def backfill_embeddings(
    repository: Repository,
    ollama_client: OllamaClient,
    model: str,
) -> int:
    """Backfill embeddings for all unembedded memories. Returns count embedded."""
    unembedded = await repository.get_unembedded_memories()
    if not unembedded:
        return 0

    count = 0
    for i in range(0, len(unembedded), BATCH_SIZE):
        batch = unembedded[i : i + BATCH_SIZE]
        valid = [(mem_id, content[:_MAX_EMBED_CHARS]) for mem_id, content in batch if content]
        if not valid:
            continue
        ids, texts = zip(*valid, strict=False)
        try:
            embeddings = await ollama_client.embed(list(texts), model=model)
            for mem_id, emb in zip(ids, embeddings, strict=False):
                await repository.save_embedding(mem_id, emb, auto_commit=False)
                count += 1
            # Single commit per batch instead of per-row
            await repository.commit()
        except Exception:
            logger.warning(
                "Failed to backfill memory batch %d-%d", i, i + len(batch), exc_info=True
            )

    if count:
        logger.info("Backfilled %d memory embeddings", count)
    return count


async def backfill_note_embeddings(
    repository: Repository,
    ollama_client: OllamaClient,
    model: str,
) -> int:
    """Backfill embeddings for all unembedded notes. Returns count embedded."""
    unembedded = await repository.get_unembedded_notes()
    if not unembedded:
        return 0

    count = 0
    for i in range(0, len(unembedded), BATCH_SIZE):
        batch = unembedded[i : i + BATCH_SIZE]
        valid = [
            (note_id, f"{title}: {content}"[:_MAX_EMBED_CHARS])
            for note_id, title, content in batch
            if title or content
        ]
        if not valid:
            continue
        ids, texts = zip(*valid, strict=False)
        try:
            embeddings = await ollama_client.embed(list(texts), model=model)
            for note_id, emb in zip(ids, embeddings, strict=False):
                await repository.save_note_embedding(note_id, emb, auto_commit=False)
                count += 1
            # Single commit per batch instead of per-row
            await repository.commit()
        except Exception:
            logger.warning("Failed to backfill note batch %d-%d", i, i + len(batch), exc_info=True)

    if count:
        logger.info("Backfilled %d note embeddings", count)
    return count
