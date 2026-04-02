# Guías de Testing Manual

Cada archivo en este directorio cubre el testing manual de una feature específica.

## Guías disponibles

| Feature | Archivo |
|---|---|
| Testing completo del sistema | [`manual_testing_guide.md`](../../manual_testing_guide.md) *(en la raíz del proyecto)* |
| Chat Funcional (Fase 1) | [`01-chat_funcional_testing.md`](01-chat_funcional_testing.md) |
| Conversation Skill (Fase 1) | [`02-conversation_skill_testing.md`](02-conversation_skill_testing.md) |
| Persistencia y Memoria (Fase 2) | [`03-persistencia_memoria_testing.md`](03-persistencia_memoria_testing.md) |
| UX y Multimedia (Fase 3) | [`05-ux_multimedia_testing.md`](05-ux_multimedia_testing.md) |
| Skills y Herramientas (Fase 4) | [`06-skills_herramientas_testing.md`](06-skills_herramientas_testing.md) |
| Memoria Avanzada (Fase 5) | [`07-memoria_avanzada_testing.md`](07-memoria_avanzada_testing.md) |
| Context Engineering (Fase 5) | [`08-context_engineering_testing.md`](08-context_engineering_testing.md) |
| Búsqueda Semántica (Fase 6) | [`09-busqueda_semantica_testing.md`](09-busqueda_semantica_testing.md) |
| CI/CD (Fase 7) | [`10-cicd_testing.md`](10-cicd_testing.md) |
| Guardrails y Trazabilidad (Fase 8) | [`12-eval_guardrails_tracing_testing.md`](12-eval_guardrails_tracing_testing.md) |
| Dataset Vivo (Fase 8) | [`13-eval_dataset_testing.md`](13-eval_dataset_testing.md) |
| User Signals (Fase 8) | [`14-eval_user_signals_testing.md`](14-eval_user_signals_testing.md) |
| Auto-evolución (Fase 8) | [`15-eval_auto_evolution_testing.md`](15-eval_auto_evolution_testing.md) |
| Eval Skill (Fase 8) | [`16-eval_skill_testing.md`](16-eval_skill_testing.md) |
| Maduración del Pipeline (Fase 8) | [`17-eval_maduracion_testing.md`](17-eval_maduracion_testing.md) |
| Sesiones Agénticas | [`18-agentic_sessions.md`](18-agentic_sessions.md) |
| Autonomous Agent (Shell, Loop Detection) | [`19-autonomous_agent_testing.md`](19-autonomous_agent_testing.md) |
| Web Browsing & URL Fetching | [`22-web_browsing_testing.md`](22-web_browsing_testing.md) |
| Observability & Tracing Profundo | [`24-observability_testing.md`](24-observability_testing.md) |
| Agentic Security Layer | [`23-agentic_security_testing.md`](23-agentic_security_testing.md) |
| Autonomous Agent Sprint 2 | [`20-autonomous_agent_sprint2_testing.md`](20-autonomous_agent_sprint2_testing.md) |
| Autonomous Agent Sprint 3 | [`21-autonomous_agent_sprint3_testing.md`](21-autonomous_agent_sprint3_testing.md) |
| Web Fetch Fix — Puppeteer-first + mcp-fetch fallback | [`25-web_fetch_fix_testing.md`](25-web_fetch_fix_testing.md) |
| Dynamic Tool Budget & `request_more_tools` | [`27-dynamic_tool_budget_testing.md`](27-dynamic_tool_budget_testing.md) |
| Planner-Orchestrator | [`28-planner_orchestrator_testing.md`](28-planner_orchestrator_testing.md) |
| Observabilidad de Agentes (singleton, spans, tokens) | [`29-observability_testing.md`](29-observability_testing.md) |
| Eval Stack Hardening (remediation bilingüe, LLM-as-judge, benchmark offline) | [`30-eval_hardening_testing.md`](30-eval_hardening_testing.md) |
| Context Engineering v2 (token budget, XML, windowing, capabilities filter, memory threshold, scratchpad) | [`31-context_engineering_v2_testing.md`](31-context_engineering_v2_testing.md) |
| Prompt Engineering & Versioning (think=False, few-shot, prompt registry, eval-coupled versioning, /prompts command) | [`32-prompt_engineering_testing.md`](32-prompt_engineering_testing.md) |
| Telegram Integration (multi-platform, PlatformClient Protocol, Adapter pattern) | [`35-telegram_testing.md`](35-telegram_testing.md) |
| Performance Optimization (latency, indexes, event loop, startup) | [`36-performance_testing.md`](36-performance_testing.md) |
| Ontology Data Model / Knowledge Graph (entities, relations, BFS traversal, enrichment) | [`42-ontology_testing.md`](42-ontology_testing.md) |
| Data Provenance & Lineage (audit log, memory versioning, lineage tools) | [`44-data_provenance_testing.md`](44-data_provenance_testing.md) |
| Token Accuracy (runtime calibration, EMA per-model) | [`45-token_accuracy_testing.md`](45-token_accuracy_testing.md) |
| Deployment Maturity (liveness, readiness, Docker healthcheck, profiles, release) | [`46-deployment_maturity_testing.md`](46-deployment_maturity_testing.md) |
| Operational Automation (data-driven triggers, metric alerts, self-healing) | [`47-operational_automation_testing.md`](47-operational_automation_testing.md) |
| Regression Eval Suite (seed, classify/tools/e2e, CI-compatible) | [`49-regression_eval_suite_testing.md`](49-regression_eval_suite_testing.md) |
| Deep Web Research (composite search+fetch+chunk+embed+rank) | [`51-web_research_testing.md`](51-web_research_testing.md) |
| Web Search Enhanced (depth param, auto-fetch, LLM extraction) | [`52-web_search_enhanced_testing.md`](52-web_search_enhanced_testing.md) |

## Convenciones

- Un archivo por feature o área funcional
- Usar `TEMPLATE.md` como punto de partida
- Nombre del archivo: `<nombre>_testing.md`
- Cada guía debe incluir: casos felices, edge cases, verificación en logs, queries de DB si aplica

## Nota sobre el testing general

La guía completa de testing del sistema está en `manual_testing_guide.md` (raíz del proyecto).
Las guías en este directorio son específicas por feature, creadas al implementar cada una.
