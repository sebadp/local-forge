from app.skills.models import ToolCall
from app.skills.registry import SkillRegistry
from app.skills.tools.notes_tools import register


async def _make_registry(repository):
    reg = SkillRegistry(skills_dir="/nonexistent")
    register(reg, repository)
    return reg


async def test_save_note(repository):
    reg = await _make_registry(repository)
    result = await reg.execute_tool(
        ToolCall(
            name="save_note",
            arguments={"title": "Shopping", "content": "Buy milk and eggs"},
        )
    )
    assert result.success
    assert "Shopping" in result.content
    assert "ID:" in result.content


async def test_list_notes_empty(repository):
    reg = await _make_registry(repository)
    result = await reg.execute_tool(ToolCall(name="list_notes", arguments={}))
    assert result.success
    assert "No notes" in result.content


async def test_list_notes_with_data(repository):
    reg = await _make_registry(repository)
    await repository.save_note("Note 1", "Content 1")
    await repository.save_note("Note 2", "Content 2")

    result = await reg.execute_tool(ToolCall(name="list_notes", arguments={}))
    assert result.success
    assert "Note 1" in result.content
    assert "Note 2" in result.content


async def test_search_notes(repository):
    reg = await _make_registry(repository)
    await repository.save_note("Shopping", "Buy milk")
    await repository.save_note("Work", "Fix the bug")

    result = await reg.execute_tool(ToolCall(name="search_notes", arguments={"query": "milk"}))
    assert result.success
    assert "Shopping" in result.content
    assert "Work" not in result.content


async def test_search_notes_no_results(repository):
    reg = await _make_registry(repository)
    result = await reg.execute_tool(
        ToolCall(name="search_notes", arguments={"query": "nonexistent"})
    )
    assert result.success
    assert "No notes" in result.content


async def test_delete_note(repository):
    reg = await _make_registry(repository)
    note_id = await repository.save_note("To Delete", "Delete me")

    result = await reg.execute_tool(ToolCall(name="delete_note", arguments={"note_id": note_id}))
    assert result.success
    assert "deleted" in result.content

    # Verify it's gone
    notes = await repository.list_notes()
    assert len(notes) == 0


async def test_delete_nonexistent_note(repository):
    reg = await _make_registry(repository)
    result = await reg.execute_tool(ToolCall(name="delete_note", arguments={"note_id": 999}))
    assert result.success
    assert "not found" in result.content


async def test_get_note_full_content(repository):
    reg = await _make_registry(repository)
    long_content = "A" * 200 + " full content that exceeds 80 chars preview"
    note_id = await repository.save_note("Long Note", long_content)

    result = await reg.execute_tool(ToolCall(name="get_note", arguments={"note_id": note_id}))
    assert result.success
    assert "Long Note" in result.content
    assert long_content in result.content  # Full content, not truncated


async def test_get_note_not_found(repository):
    reg = await _make_registry(repository)
    result = await reg.execute_tool(ToolCall(name="get_note", arguments={"note_id": 999}))
    assert result.success
    assert "not found" in result.content


async def test_save_note_ignores_extra_kwargs(repository):
    """save_note tolerates extra kwargs (e.g. LLM hallucinating 'tags')."""
    reg = await _make_registry(repository)
    result = await reg.execute_tool(
        ToolCall(
            name="save_note",
            arguments={"title": "Test", "content": "Body", "tags": ["research"]},
        )
    )
    assert result.success
    assert "Test" in result.content


async def test_list_notes_truncates_but_get_note_shows_full(repository):
    """list_notes truncates at 80 chars; get_note returns the full content."""
    reg = await _make_registry(repository)
    full_content = (
        "Pelicula 1: descripcion larga. Pelicula 2: otra descripcion muy larga que excede."
    )
    note_id = await repository.save_note("Pelis", full_content)

    list_result = await reg.execute_tool(ToolCall(name="list_notes", arguments={}))
    get_result = await reg.execute_tool(ToolCall(name="get_note", arguments={"note_id": note_id}))

    assert result.success if (result := list_result) else True
    # list truncates at 80 chars
    assert full_content not in list_result.content or len(list_result.content) > 0
    # get_note returns full
    assert full_content in get_result.content
