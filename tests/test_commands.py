from app.commands.builtins import (
    cmd_clear,
    cmd_forget,
    cmd_help,
    cmd_memories,
    cmd_remember,
    cmd_reset_metrics,
)
from app.commands.context import CommandContext
from app.commands.parser import parse_command

# --- Parser tests ---


def test_parse_command_remember():
    result = parse_command("/remember mi cumple es el 15")
    assert result == ("remember", "mi cumple es el 15")


def test_parse_command_no_args():
    result = parse_command("/help")
    assert result == ("help", "")


def test_parse_command_not_a_command():
    assert parse_command("hello world") is None


def test_parse_command_empty():
    assert parse_command("") is None


def test_parse_command_just_slash():
    assert parse_command("/") is None


def test_parse_command_case_insensitive():
    result = parse_command("/REMEMBER something")
    assert result == ("remember", "something")


# --- Command handler tests ---


async def test_cmd_remember(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_remember("my birthday is March 15", ctx)
    assert "Remembered" in reply
    assert "my birthday is March 15" in reply

    memories = await repository.get_active_memories()
    assert "my birthday is March 15" in memories


async def test_cmd_remember_empty(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_remember("", ctx)
    assert "Usage" in reply


async def test_cmd_forget(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    await repository.add_memory("my birthday is March 15")
    reply = await cmd_forget("my birthday is March 15", ctx)
    assert "Forgot" in reply

    memories = await repository.get_active_memories()
    assert len(memories) == 0


async def test_cmd_forget_nonexistent(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_forget("nonexistent", ctx)
    assert "No active memory" in reply


async def test_cmd_memories(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    await repository.add_memory("Fact 1")
    await repository.add_memory("Fact 2")
    reply = await cmd_memories("", ctx)
    assert "Fact 1" in reply
    assert "Fact 2" in reply


async def test_cmd_memories_empty(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_memories("", ctx)
    assert "No memories" in reply


async def test_cmd_clear(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    conv_id = await repository.get_or_create_conversation("123")
    await repository.save_message(conv_id, "user", "hello")

    reply = await cmd_clear("", ctx)
    assert "cleared" in reply.lower()

    count = await repository.get_message_count(conv_id)
    assert count == 0


async def test_cmd_help(repository, memory_file, command_registry):
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_help("", ctx)
    assert "/remember" in reply
    assert "/forget" in reply
    assert "/memories" in reply
    assert "/clear" in reply
    assert "/help" in reply


async def test_cmd_help_with_skills(repository, memory_file, command_registry, tmp_path):
    """When skill_registry has skills loaded, /help shows them."""
    from app.skills.registry import SkillRegistry

    # Create a minimal skill directory with a SKILL.md
    skill_dir = tmp_path / "weather"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: weather\ndescription: Get current weather\ntools:\n  - get_weather\n---\nUse get_weather.\n"
    )
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    (search_dir / "SKILL.md").write_text(
        "---\nname: search\ndescription: Search the internet\ntools:\n  - web_search\n---\nUse web_search.\n"
    )

    sr = SkillRegistry(skills_dir=str(tmp_path))
    sr.load_skills()

    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
        skill_registry=sr,
    )
    reply = await cmd_help("", ctx)
    # Commands section
    assert "/help" in reply
    assert "/remember" in reply
    # Skills section
    assert "Skills" in reply
    assert "weather" in reply
    assert "search" in reply
    assert "Get current weather" in reply


async def test_cmd_help_with_mcp(repository, memory_file, command_registry):
    """When mcp_manager has tools, /help shows MCP integrations."""
    from unittest.mock import MagicMock

    from app.skills.models import ToolDefinition

    async def h(**kwargs):
        return ""

    mcp_manager = MagicMock()
    mcp_manager.get_tools_summary.return_value = "something"
    mcp_manager._server_descriptions = {"filesystem": "Read and write files"}
    tools = {
        "read_file": ToolDefinition(
            name="read_file",
            description="Read a file",
            parameters={},
            handler=h,
            skill_name="mcp::filesystem",
        ),
        "write_file": ToolDefinition(
            name="write_file",
            description="Write a file",
            parameters={},
            handler=h,
            skill_name="mcp::filesystem",
        ),
    }
    mcp_manager.get_tools.return_value = tools

    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
        mcp_manager=mcp_manager,
    )
    reply = await cmd_help("", ctx)
    assert "MCP integrations" in reply
    assert "filesystem" in reply
    assert "Read and write files" in reply
    assert "read_file" in reply
    assert "write_file" in reply


async def test_unknown_command(command_registry):
    spec = command_registry.get("nonexistent")
    assert spec is None


async def test_cmd_reset_metrics(repository, memory_file, command_registry):
    """reset-metrics clears traces and eval data but keeps memories."""
    # Setup: add a memory and a trace
    await repository.add_memory("Important fact")
    await repository.save_trace("t1", "123", "hello", "text")
    await repository.finish_trace("t1", "completed", output_text="world")

    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_reset_metrics("", ctx)
    assert "reseteadas" in reply.lower() or "cero" in reply.lower()

    # Memories preserved
    memories = await repository.get_active_memories()
    assert "Important fact" in memories

    # Traces gone
    cursor = await repository._conn.execute("SELECT COUNT(*) FROM traces")
    row = await cursor.fetchone()
    assert row[0] == 0


async def test_cmd_reset_metrics_empty(repository, memory_file, command_registry):
    """reset-metrics with no data reports zero."""
    ctx = CommandContext(
        repository=repository,
        memory_file=memory_file,
        phone_number="123",
        registry=command_registry,
    )
    reply = await cmd_reset_metrics("", ctx)
    assert "cero" in reply.lower()
