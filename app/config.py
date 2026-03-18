from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource


def _strip_inline_comment(value: Any) -> Any:
    """Strip trailing inline shell comments from unquoted env var values.

    e.g. 'FIELD=             # explanation' → value becomes '' after strip.
    Only affects string values; non-strings pass through unchanged.
    """
    if isinstance(value, str) and "#" in value:
        value = value[: value.index("#")].strip()
    return value


def _safe_decode_complex(value: Any) -> Any:
    """Strip inline comments, then attempt JSON decoding.

    Falls back to returning the raw string when JSON parsing fails, so that
    field_validators (e.g. comma-split for list[str] fields) can handle it.
    """
    import json as _json

    value = _strip_inline_comment(value)
    if not isinstance(value, str) or value == "":
        return None  # env_ignore_empty / field default takes over
    try:
        return _json.loads(value)
    except ValueError:
        return value  # let field_validator handle comma-separated strings


class _SafeEnvSource(EnvSettingsSource):
    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        return _safe_decode_complex(value)


class _SafeDotEnvSource(DotEnvSettingsSource):
    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        return _safe_decode_complex(value)


class Settings(BaseSettings):
    # WhatsApp Cloud API
    whatsapp_access_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str
    allowed_phone_numbers: list[str]

    @field_validator("allowed_phone_numbers", mode="before")
    @classmethod
    def parse_phone_numbers(cls, v: object) -> object:
        if isinstance(v, str):
            return [n.strip() for n in v.split(",") if n.strip()]
        if isinstance(v, int | float):
            return [str(int(v))]
        return v

    # Ollama
    ollama_base_url: str = "http://localhost:11435"
    ollama_model: str = "qwen3.5:9b"
    system_prompt: str = (
        "You are a helpful personal assistant on WhatsApp. "
        "Be friendly. Answer in the same language the user writes in. "
        "Adapt your response length to the user's request — be brief for simple questions, "
        "detailed when asked for long or thorough answers. "
        "TOOL AWARENESS: You have REAL web search and page fetching tools. "
        "When you use a tool and receive results, present those results directly to the user. "
        "NEVER say you cannot access the internet or lack web search capabilities. "
        "CRITICAL: When the user provides a URL and you have URL-reading tools available, "
        "ALWAYS use them to fetch the content before responding. "
        "Do NOT assume a page is inaccessible without trying the tool first. "
        "GROUNDING RULE: Never fabricate specific facts (tech stacks, percentages, metrics, "
        "file contents) without reading actual data via tools first. If you only have partial "
        "information (e.g. a directory listing), say what you see and use tools to read key "
        "files (README, config files, package.json, requirements.txt) before making claims. "
        "If a tool call fails, report the error honestly — do not invent the answer. "
        "When asked about current events, recent software versions, or news, "
        "ALWAYS use search or fetch tools. Never answer from memory for time-sensitive topics. "
        "Always provide a natural language response after using tools — never return an empty message."
    )
    conversation_max_messages: int = 20

    # Database
    database_path: str = "data/localforge.db"
    summary_threshold: int = 40
    compaction_threshold: int = 20000
    history_verbatim_count: int = 8  # Last N messages verbatim; older ones replaced by summary

    # ngrok (only used in docker-compose, not by the app itself)
    ngrok_authtoken: str = ""
    ngrok_domain: str = ""

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # Rate limiting
    rate_limit_max: int = 10
    rate_limit_window: int = 60

    # Audio (Whisper)
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # Vision
    vision_model: str = "llava:7b"

    # Skills
    skills_dir: str = "skills"

    # MCP
    mcp_config_path: str = "data/mcp_servers.json"

    # Tool router
    max_tools_per_call: int = 8

    # Memory (Phase 5)
    memory_dir: str = "data/memory"
    daily_log_days: int = 2
    memory_flush_enabled: bool = True

    # Embeddings & Semantic Search (Phase 6)
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    semantic_search_enabled: bool = True
    semantic_search_top_k: int = 10
    memory_file_watch_enabled: bool = True
    memory_similarity_threshold: float = (
        1.0  # L2 distance threshold; 1.0 = accept all (tune with real data)
    )

    # User profiles & onboarding (Phase 8)
    onboarding_enabled: bool = True
    profile_discovery_interval: int = 10  # messages between progressive discovery runs

    # Guardrails (Fase 1)
    guardrails_enabled: bool = True
    guardrails_language_check: bool = True
    guardrails_default_language: str = "es"  # Fallback when user_text too short for detection
    guardrails_pii_check: bool = True
    guardrails_llm_checks: bool = False  # Activar en Iteración 6
    guardrails_llm_timeout: float = 3.0  # segundos; 0.5 era demasiado bajo para qwen3.5:9b local

    # Tracing (Fase 2)
    tracing_enabled: bool = True
    tracing_sample_rate: float = 1.0  # 1.0 = trace everything
    trace_retention_days: int = 90
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://localhost:3000"

    # Evaluation (Fase 3+)
    eval_auto_curate: bool = True

    # Prompt versioning (Exec Plan 32)
    prompt_versioning_enabled: bool = True  # Seed & track prompt versions in DB

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    allowed_telegram_chat_ids: list[str] = []
    telegram_enabled: bool = False
    telegram_webhook_url: str = ""  # If set, app auto-registers the webhook at startup

    @field_validator("allowed_telegram_chat_ids", mode="before")
    @classmethod
    def parse_telegram_chat_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [n.strip() for n in v.split(",") if n.strip()]
        return v

    # Agent Mode
    agent_write_enabled: bool = False  # Habilita write tools (seguridad: OFF por defecto)
    agent_max_iterations: int = 15  # Límite de iteraciones por sesión agéntica
    agent_session_timeout: int = 300  # Timeout en segundos (5 minutos)
    agent_shell_allowlist: str = (
        "pytest,ruff,mypy,make,npm,git,cat,head,tail,wc,ls,find,grep,echo,python,node"
    )
    github_token: str | None = None
    github_repo: str | None = None
    audit_hmac_key: str | None = None  # HMAC-SHA256 key for tamper-evident audit trail
    projects_root: str = ""  # Base directory for multi-project workspace (empty = single project)

    # Metrics (Plan 42)
    metrics_percentiles_enabled: bool = True
    # NOTE: Percentile calculation loads all values into Python memory.
    # Disable via METRICS_PERCENTILES_ENABLED=false if memory becomes an issue
    # (e.g. when span count exceeds ~100K).

    # Web Search enhanced (Plan 52)
    web_search_fetch_top_n: int = 3  # pages to fetch in detailed mode
    web_search_fetch_timeout: float = 8.0  # per-page fetch timeout (seconds)
    web_search_extract_page_limit: int = 2500  # chars per page sent to LLM extraction

    # Ontology (Plan 42)
    ontology_enabled: bool = True

    # Data Provenance (Plan 44)
    provenance_enabled: bool = True

    # Operational Automation (Plan 47)
    automation_enabled: bool = False
    automation_interval_minutes: int = 15
    automation_admin_phone: str = ""

    model_config = {"env_file": ".env", "env_ignore_empty": True}

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):  # type: ignore[override]
        # Replace default sources with comment-stripping wrappers so that
        # inline comments in .env files (e.g. "FIELD=  # explanation") don't
        # crash JSON decoding for list/dict fields.
        safe_env = _SafeEnvSource(settings_cls)
        safe_dotenv = _SafeDotEnvSource(
            settings_cls, env_file=settings_cls.model_config.get("env_file")
        )
        return (init_settings, safe_env, safe_dotenv, file_secret_settings)
