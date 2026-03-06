# Walkthroughs de Features

Cada archivo en este directorio documenta una feature implementada: qué hace, cómo funciona internamente, cómo extenderla.

## Convenciones

- Un archivo por feature o fase significativa
- Usar `TEMPLATE.md` como punto de partida
- Nombre del archivo: `<nombre-en-kebab-case>.md`
- Linkear siempre a la guía de testing correspondiente en `docs/testing/`

## Features documentadas

| Feature | Archivo | Fase |
|---|---|---|
| Chat Funcional | [`01-chat_funcional.md`](01-chat_funcional.md) | Fase 1 |
| Conversation Skill & Auto Debug | [`02-conversation_skill.md`](02-conversation_skill.md) | Fase 1 |
| Persistencia y Memoria | [`03-persistencia_memoria.md`](03-persistencia_memoria.md) | Fase 2 |
| Context Compaction (LLM-based) | [`04-context_compaction.md`](04-context_compaction.md) | Fase 2 |
| UX y Multimedia | [`05-ux_multimedia.md`](05-ux_multimedia.md) | Fase 3 |
| Skills y Herramientas | [`06-skills_herramientas.md`](06-skills_herramientas.md) | Fase 4 |
| Memoria Avanzada | [`07-memoria_avanzada.md`](07-memoria_avanzada.md) | Fase 5 |
| Context Engineering | [`08-context_engineering.md`](08-context_engineering.md) | Fase 5 |
| Búsqueda Semántica | [`09-busqueda_semantica.md`](09-busqueda_semantica.md) | Fase 6 |
| CI/CD | [`10-cicd.md`](10-cicd.md) | Fase 7 |
| Guardrails y Trazabilidad | [`12-eval_guardrails_tracing.md`](12-eval_guardrails_tracing.md) | Fase 8 |
| Dataset Vivo | [`13-eval_dataset.md`](13-eval_dataset.md) | Fase 8 |
| User Signals | [`14-eval_user_signals.md`](14-eval_user_signals.md) | Fase 8 |
| Auto-evolución | [`15-eval_auto_evolution.md`](15-eval_auto_evolution.md) | Fase 8 |
| Eval Skill | [`16-eval_skill.md`](16-eval_skill.md) | Fase 8 |
| Maduración del Pipeline | [`17-eval_maduracion.md`](17-eval_maduracion.md) | Fase 8 |
| Sesiones Agénticas | [`18-agentic_sessions.md`](18-agentic_sessions.md) | Agent Mode |
| Autonomous Agent (Shell, Loop Detection) | [`19-autonomous_agent.md`](19-autonomous_agent.md) | Agent Mode |
| Web Browsing & URL Fetching | [`22-web_browsing.md`](22-web_browsing.md) | Agent Mode |
| Agentic Security Layer | [`23-agentic_security.md`](23-agentic_security.md) | Agent Mode |
| Observability & Tracing Profundo | [`24-observability.md`](24-observability.md) | Fase 8 |
| Autonomous Agent Sprint 2 (Diff, PR, Persistence, Bootstrap) | [`20-autonomous_agent_sprint2.md`](20-autonomous_agent_sprint2.md) | Agent Mode |
| Autonomous Agent Sprint 3 (Cron, Outline, Workspace) | [`21-autonomous_agent_sprint3.md`](21-autonomous_agent_sprint3.md) | Agent Mode |
| Web Fetch Fix — Puppeteer-first + mcp-fetch fallback | [`25-web_fetch_fix.md`](25-web_fetch_fix.md) | Agent Mode |
| Dynamic Tool Budget & `request_more_tools` | [`27-dynamic_tool_budget.md`](27-dynamic_tool_budget.md) | Agent Mode |
| Planner-Orchestrator | [`28-planner_orchestrator.md`](28-planner_orchestrator.md) | Agent Mode |
| Observabilidad de Agentes (singleton, spans, tokens) | [`29-observability.md`](29-observability.md) | Agent Mode |
| Eval Stack Hardening (remediation bilingüe, LLM-as-judge, benchmark offline) | [`30-eval_hardening.md`](30-eval_hardening.md) | Eval |
| Context Engineering v2 (token budget, XML consolidation, history windowing, capabilities filtering, memory threshold, scratchpad) | [`31-context_engineering_v2.md`](31-context_engineering_v2.md) | Context |
| Prompt Engineering & Versioning (think=False, few-shot, prompt registry, eval-coupled versioning, /prompts command) | [`32-prompt_engineering.md`](32-prompt_engineering.md) | Eval |
| Telegram Integration (multi-platform, PlatformClient Protocol, Adapter pattern) | [`35-telegram.md`](35-telegram.md) | Exec Plan 35 |

## Cómo crear un nuevo walkthrough

1. Copiar `TEMPLATE.md` con el nombre de la feature
2. Completar todas las secciones (especialmente "Decisiones de diseño" y "Gotchas")
3. Agregar la fila correspondiente a la tabla de arriba
4. Crear la guía de testing en `docs/testing/<nombre>_testing.md`
