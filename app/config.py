from pydantic import field_validator
from pydantic_settings import BaseSettings


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
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    system_prompt: str = (
        "You are a helpful personal assistant on WhatsApp. "
        "Be friendly. Answer in the same language the user writes in. "
        "Adapt your response length to the user's request — be brief for simple questions, "
        "detailed when asked for long or thorough answers. "
        "CRITICAL: When the user provides a URL and you have URL-reading tools available, "
        "ALWAYS use them to fetch the content before responding. "
        "Do NOT assume a page is inaccessible without trying the tool first."
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
    guardrails_pii_check: bool = True
    guardrails_llm_checks: bool = False  # Activar en Iteración 6
    guardrails_llm_timeout: float = 3.0  # segundos; 0.5 era demasiado bajo para qwen3:8b local

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

    model_config = {"env_file": ".env"}
