# Informe Comparativo: Claude Code vs LocalForge — Guardrails, Evals & Telemetry

> **Fecha**: 2026-04-02
> **Propósito**: Evaluar qué patrones usa Claude Code en guardrails, evals y telemetry, comparar con lo que LocalForge ya tiene, e identificar gaps accionables.

---

## 1. GUARDRAILS & SAFETY

### 1.1 Claude Code: Cómo funciona

Claude Code tiene un sistema de seguridad en **3 capas**:

| Capa | Mecanismo | Descripción |
|------|-----------|-------------|
| **Hooks** | PreToolUse / PostToolUse / UserPromptSubmit | Scripts (bash/python) o prompts LLM que interceptan acciones. Exit code 2 = bloqueo. Pueden modificar inputs (`updatedInput`), aprobar/denegar/escalar (`permissionDecision: allow|deny|ask|defer`). 9+ event types. |
| **Permissions** | `permissions.ask` / `permissions.deny` / `defaultMode` | Configuración JSON que define qué tools requieren aprobación, cuáles están bloqueados. Enterprise: `allowManagedPermissionRulesOnly` impide que el usuario override. |
| **Sandbox** | macOS sandbox / Linux namespace | Bash tool ejecuta en sandbox con allowlist de dominios de red, bloqueo de puertos locales, proxy HTTP/SOCKS. `failIfUnavailable` mata el proceso si no puede sandboxear. |

**Checks de contenido específicos:**
- Hook `security_reminder_hook.py`: detecta patrones peligrosos en Edit/Write — GitHub Actions injection, `eval()`, `child_process.exec`, `dangerouslySetInnerHTML`, `pickle`, `os.system`
- Hook `validate-write.sh`: bloquea path traversal (`..`), dirs de sistema (`/etc`, `/sys`), archivos sensibles (`.env`, `secret`, `credentials`)
- Hook `validate-bash.sh`: bloquea `rm -rf`, `dd`, `mkfs`, writes a `/dev/`; escala `sudo`/`su` a modo ask
- `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`: limpia credenciales de subprocesos

**Modelo de confianza:**
- El sandbox aplica **solo a Bash**, no a Read/Write/MCP
- Los hooks pueden ser determinísticos (scripts) o LLM-driven (para decisiones contextuales)
- Enterprise puede bloquear hooks y permissions del usuario (`allowManagedHooksOnly`)

### 1.2 LocalForge: Qué tenemos

| Capa | Mecanismo | Descripción |
|------|-----------|-------------|
| **Guardrails Pipeline** | `app/guardrails/pipeline.py` | 5 checks determinísticos (empty, length>8000, raw JSON leak, language match, PII) + 2 LLM opcionales (coherence, hallucination). Fail-open. Post-LLM. |
| **Policy Engine** | `app/security/policy_engine.py` | YAML rules con regex matching por tool + argumentos. Acciones: ALLOW/BLOCK/FLAG. Fail-secure (default BLOCK si falta config). |
| **Shell Validation** | `app/skills/tools/shell_tools.py` | `_validate_command()`: denylist → dangerous patterns → allowlist + arg validation → HITL. 4 capas. |
| **Path Validation** | `app/skills/tools/selfcode_tools.py` | `_is_safe_path()`: project root check, blocked name patterns, blocked extensions, blocked config files. |
| **Audit Trail** | `app/security/audit.py` | Append-only JSONL con SHA-256 chain + HMAC opcional. Tamper-evident. |
| **HITL** | `app/agent/hitl.py` | Aprobación via WhatsApp con timeout de 2 min. Per-user asyncio.Event. |

### 1.3 Comparación directa

| Capacidad | Claude Code | LocalForge | Gap |
|-----------|------------|------------|-----|
| **Pre-tool interception** | Hooks (PreToolUse) — scripts o LLM | Policy Engine (YAML regex) | CC es mas flexible (scripts arbitrarios + LLM-driven). LF es declarativo (YAML). **Oportunidad: hooks system.** |
| **Post-tool interception** | Hooks (PostToolUse) | Guardrails pipeline (post-LLM, no post-tool) | LF no valida resultados de tools individuales. **Gap medio.** |
| **Input interception** | Hooks (UserPromptSubmit) | Nada | LF no valida input del usuario pre-LLM. **Gap menor** (WhatsApp limita vectors de ataque). |
| **Sandbox de ejecución** | macOS/Linux OS-level sandbox para Bash | `_validate_command()` denylist + allowlist | CC usa sandbox de OS. LF valida en app-level. **Gap significativo para producción.** |
| **Content security patterns** | Detecta eval(), injection, XSS, pickle | `check_no_pii()` detecta tokens/emails. No detecta code patterns. | **Gap: no detectamos patterns de código inseguro en outputs.** |
| **Permission management** | JSON configurable por usuario/enterprise | YAML policy + hardcoded denylist | CC es mas granular y configurable. LF es mas rígido. **Gap menor.** |
| **Credential scrubbing** | `ENV_SCRUB` limpia credenciales de subprocesos | `_SENSITIVE` set oculta en runtime config display | LF oculta en display pero no scrubea env de subprocesos. **Gap medio.** |
| **Audit trail** | No documentado publicamente | SHA-256 chain + HMAC. **LF es superior aquí.** | CC no tiene audit trail público. |
| **HITL approval** | Modal en terminal | WhatsApp message + timeout | Equivalente adaptado al medio. **Paridad.** |
| **Fail-open vs fail-secure** | Hooks fail-open (exit 0 = allow) | Guardrails fail-open, Policy Engine fail-secure | **LF tiene ambos modelos, bien diseñado.** |

---

## 2. EVALS & BENCHMARKS

### 2.1 Claude Code: Cómo funciona

**El eval code es 100% interno (no público).** Lo que sabemos del blog de engineering:

| Componente | Descripción |
|-----------|-------------|
| **Code-based graders** | Checks determinísticos sobre file ops y edits (¿el archivo se creó? ¿el test pasa?) |
| **LLM-as-judge** | Modelo evalúa calidad de código y comportamiento (concisión, over-engineering, instruction following). Requiere calibración frecuente contra juicio humano. |
| **Human review** | Gold-standard. Se lee el transcript completo, no solo el score. "Nunca confiar en scores hasta leer transcripts." |
| **Métricas** | Calidad (concisión, edit accuracy), comportamiento (over-engineering), transcript (turns, tokens), latencia (TTFT, tokens/sec) |
| **SWE-bench** | CC es el harness para SWE-bench Verified. Opus 4.5: 80.9%, Sonnet 4.5: 77.2% |
| **A/B testing** | Blind A/B para skills. Detecta regresiones silenciosas (ej: CRM skill pone revenue en columna incorrecta post-model-update). |
| **Phased approach** | Empiezan con feedback manual → evals en áreas narrow → expanden a comportamientos complejos |

### 2.2 LocalForge: Qué tenemos

| Componente | Archivo | Descripción |
|-----------|---------|-------------|
| **Dataset curation** | `app/eval/dataset.py` | 3-tier auto-curation: failure (<0.3), golden (≥0.8 + user signal), candidate (≥0.8 sin signal). Tags: `guardrail:{check}`. |
| **JSONL export** | `app/eval/exporter.py` | Export con id, trace_id, entry_type, input, output, expected, metadata |
| **Prompt evolution** | `app/eval/evolution.py` | MIPRO-like: diagnose failure → LLM propose change → save as draft → human approval |
| **LLM-as-judge** | `eval_tools.py` → `run_quick_eval()` | Binary yes/no: "Does actual answer correctly address question?" `think=False` |
| **11 eval tools** | `app/skills/tools/eval_tools.py` | summary, failures, diagnose, correction, dataset stats, latency, search, agent, dashboard |
| **Regression suite** | Plan 49 `scripts/run_eval.py` | 3 modes: classify, tools, e2e. Multi-criteria judge (correctness, completeness, tool_usage). CI-compatible. |
| **Scoring** | `TraceRecorder.add_score()` | Per-trace scores: system, user, human. Synced to Langfuse. |

### 2.3 Comparación directa

| Capacidad | Claude Code | LocalForge | Gap |
|-----------|------------|------------|-----|
| **Deterministic graders** | File ops checks, test pass/fail | `check_not_empty`, `check_language_match`, `check_no_pii`, `check_excessive_length`, `check_no_raw_tool_json` | LF tiene 5 checks determinísticos. CC probablemente tiene más pero no son públicos. **Paridad razonable.** |
| **LLM-as-judge** | Multi-criteria, calibrado contra humanos | Binary yes/no. Sin calibración contra humanos. | **Gap significativo: LF judge es simplista.** Un solo criterio binario no captura calidad de código, concisión, over-engineering. |
| **Human review loop** | Gold-standard. Leen transcripts. | `propose_correction()` + prompt evolution con draft/approve. | LF tiene el mecanismo pero no hay documentación de que se use activamente. **Gap operacional.** |
| **Regression testing** | Interno. A/B testing por skill. | `run_eval.py` con 3 modos. CI-compatible. | **LF tiene buen framework.** Falta dataset real y uso en CI. |
| **SWE-bench** | Harness oficial | No aplica (WhatsApp assistant, no coding agent puro) | No es un gap relevante para el producto. |
| **A/B testing** | Blind comparison pre/post prompt change | No | **Gap: no hay forma de comparar dos versiones de prompt side-by-side.** |
| **Auto-curation** | No documentado públicamente | 3-tier con tags automáticos. **LF es potencialmente superior aquí.** | LF tiene auto-curation que CC no documenta. |
| **Prompt evolution** | No documentado públicamente | MIPRO-like con draft/approve. **LF es innovador aquí.** | LF tiene prompt evolution automática. |
| **Dataset management** | No documentado | JSONL export, Langfuse sync, correction pairs. | **LF tiene buena infra de dataset.** |

---

## 3. TELEMETRY & OBSERVABILITY

### 3.1 Claude Code: Cómo funciona

| Componente | Descripción |
|-----------|-------------|
| **Usage analytics** | Code acceptance/rejection, conversaciones, feedback. Enviado a Anthropic. Opt-out disponible. |
| **OpenTelemetry** | First-class OTEL: traces, metrics, logs. Exporters configurables. Enterprise: export a su propio stack. |
| **OTEL events** | `tool_decision`, `tool_result`, spans con atributo `speed`. Resource attrs: OS, arch, terminal, language. |
| **Identity** | `CLAUDE_CODE_ACCOUNT_UUID`, `CLAUDE_CODE_USER_EMAIL`, `CLAUDE_CODE_ORGANIZATION_UUID` para enriquecer telemetry. |
| **Token tracking** | `/stats` (token counts, usage graph, streak), `/cost` (session cost USD), `/context` (tokens por MCP tool, % de context window). |
| **Budget** | `--max-budget-usd` cap en SDK. |
| **Performance** | Startup optimizado, lazy-loading session history, reduced HTTP calls para analytics. |
| **MCP sanitization** | Tool names sanitizados en analytics para no exponer config del usuario. |

### 3.2 LocalForge: Qué tenemos

| Componente | Archivo | Descripción |
|-----------|---------|-------------|
| **Trace framework** | `app/tracing/recorder.py` | SQLite + Langfuse v3. Singleton. Best-effort. |
| **Context propagation** | `app/tracing/context.py` | TraceContext async ctx mgr + contextvars. Spans anidados. |
| **Span hierarchy** | `app/webhook/router.py` | phase_ab → llm:chat → tool_loop → guardrails → remediation |
| **Token tracking** | `app/context/token_estimator.py` | Runtime EMA calibration con Ollama `prompt_eval_count`. Per-model. |
| **LLM metrics** | `app/llm/client.py` | input_tokens, output_tokens, total_duration_ms, model per response. |
| **Metrics queries** | `app/database/repository.py` | 14+ métodos: latency p50/p95/p99, token consumption, tool efficiency, search hit rate, context quality, planner metrics, HITL rate, goal completion. |
| **Eval tools** | `app/skills/tools/eval_tools.py` | 11 tools para explorar métricas via conversación. |
| **Health endpoints** | `app/health/router.py` | `/health` (liveness), `/ready` (readiness: DB + Ollama). |
| **Structured logging** | `app/logging_config.py` | JSON structured logs con `extra` dict por evento. |
| **Langfuse v3** | `app/tracing/recorder.py` | Stateful spans, scores, tags, dataset sync. OTel GenAI semantic conventions. |

### 3.3 Comparación directa

| Capacidad | Claude Code | LocalForge | Gap |
|-----------|------------|------------|-----|
| **Tracing protocol** | OpenTelemetry nativo | Custom SQLite + Langfuse v3 | CC usa OTEL estándar, exportable a cualquier backend. LF usa Langfuse (excelente para LLM). **Diferente approach, ambos válidos.** |
| **Span granularity** | `tool_decision`, `tool_result`, speed | phase_ab, llm:chat, tool_loop, guardrails, agent spans | **Paridad.** LF tiene buena granulidad. |
| **Token tracking** | `/stats`, `/cost`, `/context` (live) | EMA calibration, per-response counts, budget breakdown logs | CC tiene UI interactiva. LF tiene tracking pero no dashboard en tiempo real. **Gap: no hay /stats equivalente.** |
| **Cost tracking** | `total_cost_usd`, `--max-budget-usd` | No hay tracking de costo monetario | **Gap: LF no trackea costo.** Ollama es gratis (local), pero si migran a API providers necesitarán esto. |
| **Health probes** | No documentado | `/health` + `/ready` con checks de DB y Ollama. **LF es superior.** | CC no tiene health endpoints (es CLI, no server). |
| **Metrics depth** | No documentado público | 14+ queries: latency percentiles, tool efficiency, context quality, etc. **LF es significativamente más profundo.** | LF tiene un stack de métricas impresionante. |
| **OTEL compatibility** | Nativo | No | **Gap: LF no exporta OTEL.** Langfuse es suficiente para el caso actual, pero OTEL sería necesario para integrarse con Datadog/Grafana/etc. |
| **Privacy/opt-out** | Documentado, opt-out disponible | No aplica (self-hosted) | No es un gap (LF es self-hosted, no envía datos a terceros). |
| **MCP tool sanitization** | Sanitiza nombres en analytics | No | **Gap menor.** Relevante si agregan analytics. |

---

## 4. RESUMEN EJECUTIVO

### Dónde LocalForge YA es fuerte (mantener/pulir)

| Área | Fortaleza |
|------|-----------|
| **Audit Trail** | SHA-256 chain + HMAC es más robusto que lo documentado de CC |
| **Métricas de profundidad** | 14+ queries especializadas (latency p99, tool efficiency, context rot risk, planner metrics) — CC no documenta nada comparable |
| **Auto-curation** | 3-tier dataset curation con tags automáticos — innovador |
| **Prompt evolution** | MIPRO-like con draft/approve — CC no documenta equivalente |
| **Health probes** | `/health` + `/ready` bien implementados |
| **Multi-layer security** | Policy Engine (YAML) + shell validation (4 capas) + path validation + HITL + audit = defensa en profundidad |
| **Fail-open + fail-secure** | Guardrails fail-open, Policy Engine fail-secure — diseño correcto |

### Gaps accionables (ordenados por impacto)

| # | Gap | Impacto | Esfuerzo | Descripción |
|---|-----|---------|----------|-------------|
| **G1** | LLM-as-judge simplista | Alto | Medio | El judge actual es binary yes/no. Necesita multi-criteria (correctness, completeness, conciseness, tool_usage) con rubric y calibración humana. |
| **G2** | No hay hooks system | Alto | Alto | Los hooks de CC son su mecanismo más poderoso — scripts/LLM que interceptan pre/post tool. Permitiría extensibilidad sin tocar core. |
| **G3** | No hay detección de code security patterns en output | Medio | Bajo | Agregar check de patterns: `eval()`, `os.system()`, `dangerouslySetInnerHTML`, `pickle.loads()`, SQL concatenation. Similar al `security_reminder_hook.py` de CC. |
| **G4** | No hay A/B testing de prompts | Medio | Medio | Comparar dos versiones de prompt side-by-side con el mismo dataset. La infra de eval (`run_eval.py`) ya existe — falta el harness de comparación. |
| **G5** | No hay credential scrubbing en subprocesos | Medio | Bajo | `run_command()` hereda el env completo. Limpiar `WHATSAPP_ACCESS_TOKEN`, `GITHUB_TOKEN`, etc. antes de `subprocess.run()`. |
| **G6** | Budget compaction no usa settings inyectado | Bajo | Bajo | Ya identificado en Plan 59. Fix simple. |
| **G7** | No hay OTEL export | Bajo | Medio | Langfuse es suficiente hoy. OTEL sería necesario para Datadog/Grafana. Baja prioridad. |
| **G8** | PostToolUse validation | Bajo | Medio | Validar resultados de tools después de ejecución (ej: shell output no contiene secrets). Útil pero bajo ROI actual. |
| **G9** | No hay cost tracking (USD) | Bajo | Bajo | Ollama es local/gratis. Solo relevante si migran a API providers. |

### Recomendación

**Prioridad inmediata (Plan 59 ya creado):** Fixes del Plan 58 + Edit UX improvements.

**Siguiente plan (Plan 60 candidato):** G1 (Multi-criteria judge) + G3 (Code security patterns) + G5 (Credential scrubbing). Son 3 mejoras de alto impacto con esfuerzo bajo-medio que elevan la calidad de evals y la seguridad sin cambiar la arquitectura.

**Futuro:** G2 (Hooks system) es el cambio arquitectónico más grande — convertiría a LocalForge en un sistema extensible como CC. Requiere diseño cuidadoso y un plan dedicado.
