from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.llm.client import OllamaClient
    from app.skills.registry import SkillRegistry


def register_builtin_tools(
    registry: SkillRegistry,
    repository: Repository,
    ollama_client: OllamaClient | None = None,
    embed_model: str | None = None,
    vec_available: bool = False,
    settings=None,
    mcp_manager=None,
    daily_log=None,
) -> None:
    from app.skills.tools.calculator_tools import register as register_calculator
    from app.skills.tools.datetime_tools import register as register_datetime
    from app.skills.tools.docs_tools import register as register_docs
    from app.skills.tools.expand_tools import register as register_expand
    from app.skills.tools.git_tools import register as register_git
    from app.skills.tools.news_tools import register as register_news
    from app.skills.tools.notes_tools import register as register_notes
    from app.skills.tools.project_tools import register as register_projects
    from app.skills.tools.scheduler_tools import register as register_scheduler
    from app.skills.tools.search_tools import register as register_search
    from app.skills.tools.selfcode_tools import register as register_selfcode
    from app.skills.tools.tool_manager_tools import register as register_tool_manager
    from app.skills.tools.weather_tools import register as register_weather

    register_datetime(registry)
    register_calculator(registry)
    register_weather(registry)
    register_search(registry, ollama_client=ollama_client, settings=settings)
    register_docs(registry)
    register_notes(
        registry,
        repository,
        ollama_client=ollama_client,
        embed_model=embed_model,
        vec_available=vec_available,
    )
    register_news(registry, repository)
    register_scheduler(registry)
    register_tool_manager(registry)
    register_projects(
        registry,
        repository,
        daily_log=daily_log,
        ollama_client=ollama_client,
        embed_model=embed_model,
        vec_available=vec_available,
    )
    if settings is not None:
        register_selfcode(
            registry,
            settings,
            ollama_client=ollama_client,
            vec_available=vec_available,
        )
    if settings is not None and mcp_manager is not None:
        register_expand(registry, mcp_manager, settings)
    if settings is not None and settings.tracing_enabled:
        from app.skills.tools.eval_tools import register as register_eval

        register_eval(registry, repository, ollama_client=ollama_client, settings=settings)

    if settings is not None and settings.tracing_enabled:
        from app.skills.tools.debug_tools import register as register_debug

        register_debug(registry, repository)

    register_git(registry, settings=settings)

    if settings is not None:
        from app.skills.tools.shell_tools import register as register_shell

        register_shell(registry, settings)

    if settings is not None:
        from app.skills.tools.workspace_tools import register as register_workspace

        register_workspace(registry, projects_root=getattr(settings, "projects_root", ""))

    if settings is not None and getattr(settings, "automation_enabled", False):
        from app.skills.tools.automation_tools import register as register_automation

        register_automation(registry, repository)
