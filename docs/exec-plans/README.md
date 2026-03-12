# Planes de Ejecución

Documentos técnicos que bajan una intención de producto a cambios concretos en el codebase.

## Planes disponibles

| Plan | Archivo | Estado |
|---|---|---|
| Arquitectura de Evaluación y Mejora Continua | [`11-eval_implementation_plan.md`](11-eval_implementation_plan.md) | 📋 Pendiente |
| Sesiones Agénticas (Agent Mode) | [`18-agentic_sessions_plan.md`](18-agentic_sessions_plan.md) | ✅ Completado |
| Context Engineering | [`08-context_engineering_plan.md`](08-context_engineering_plan.md) | ✅ Completado |
| Claude Code Experience | [`EX-claude_code_experience.md`](EX-claude_code_experience.md) | 📋 Evaluación |
| OpenClaw Experience | [`EX-openclaw_experience.md`](EX-openclaw_experience.md) | 📋 Evaluación |
| **Autonomous Agent Experience** | [`19-autonomous_agent_plan.md`](19-autonomous_agent_plan.md) | **✅ Sprint 1 done** |
| Autonomous Agent Sprint 2 | [`20-autonomous_agent_sprint2_plan.md`](20-autonomous_agent_sprint2_plan.md) | **✅ Completado** |
| Autonomous Agent Sprint 3 | [`21-autonomous_agent_sprint3_plan.md`](21-autonomous_agent_sprint3_plan.md) | **✅ Completado** |
| Dynamic Tool Budget & `request_more_tools` | [`27-dynamic_tool_budget_prd.md`](27-dynamic_tool_budget_prd.md) / [`27-dynamic_tool_budget_prp.md`](27-dynamic_tool_budget_prp.md) | ✅ Completado |
| Planner-Orchestrator | [`28-planner_orchestrator_prd.md`](28-planner_orchestrator_prd.md) / [`28-planner_orchestrator_prp.md`](28-planner_orchestrator_prp.md) | 🚧 En progreso |
| Observabilidad de Agentes | [`29-observability_prd.md`](29-observability_prd.md) / [`29-observability_prp.md`](29-observability_prp.md) | ✅ Completado |
| Eval Stack Hardening | [`30-eval_hardening_prd.md`](30-eval_hardening_prd.md) / [`30-eval_hardening_prp.md`](30-eval_hardening_prp.md) | ✅ Completado |
| Context Engineering v2 | [`31-context_engineering_v2_prd.md`](31-context_engineering_v2_prd.md) / [`31-context_engineering_v2_prp.md`](31-context_engineering_v2_prp.md) | ✅ Completado |
| Prompt Engineering & Versioning | [`32-prompt_engineering_prd.md`](32-prompt_engineering_prd.md) / [`32-prompt_engineering_prp.md`](32-prompt_engineering_prp.md) | ✅ Completado |
| Fase 9 Completion — Reaction→Curation Loop | [`33-fase9_completion_prd.md`](33-fase9_completion_prd.md) / [`33-fase9_completion_prp.md`](33-fase9_completion_prp.md) | ✅ Completado |
| Security Hardening — Agent Shell & Tool Exposure | [`34-security-hardening_prd.md`](34-security-hardening_prd.md) / [`34-security-hardening_prp.md`](34-security-hardening_prp.md) | ✅ Completado |
| Performance Optimization — Latencia del Critical Path | [`36-performance_optimization_prd.md`](36-performance_optimization_prd.md) / [`36-performance_optimization_prp.md`](36-performance_optimization_prp.md) | ✅ Completado |
| Metrics Hardening — Token breakdown, latencias p95, semantic hit rate | [`38-metrics_hardening_prd.md`](38-metrics_hardening_prd.md) / [`38-metrics_hardening_prp.md`](38-metrics_hardening_prp.md) | ✅ Completado |
| Agent Metrics & Efficacy — Tool efficiency, context quality, agent efficacy | [`39-agent_metrics_prd.md`](39-agent_metrics_prd.md) / [`39-agent_metrics_prp.md`](39-agent_metrics_prp.md) | 📋 Pendiente |
| Testing Bugfix Sprint — 19 bugs de sesion real (search, agent, routing, datetime, MCP) | [`40-testing_bugfix_prd.md`](40-testing_bugfix_prd.md) / [`40-testing_bugfix_prp.md`](40-testing_bugfix_prp.md) | ✅ Completado |
| Project Notes & Tool RAG — Persistencia de contenido + descubrimiento semántico de tools | [`41-project_notes_tool_rag_prd.md`](41-project_notes_tool_rag_prd.md) / [`41-project_notes_tool_rag_prp.md`](41-project_notes_tool_rag_prp.md) | 📋 Pendiente |
| Mejoras Arquitectónicas (Palantir AIP Gap Analysis) — Quick Wins + Ontology | [`42-architecture_action_plan.md`](42-architecture_action_plan.md) / [`42-ontology_data_model_prd.md`](42-ontology_data_model_prd.md‎) | ✅ Completado |
| Langfuse v3 SDK Upgrade — Migración de API v2→v3 low-level | [`43-langfuse_v3_prd.md`](43-langfuse_v3_prd.md) / [`43-langfuse_v3_prp.md`](43-langfuse_v3_prp.md) | 📋 Planificado |
| Data Provenance & Lineage — Audit log, source tracing, memory versioning | [`44-data_provenance_prd.md`](44-data_provenance_prd.md) / [`44-data_provenance_prp.md`](44-data_provenance_prp.md) | ✅ Completado |
| Token Accuracy — Runtime calibration via Ollama prompt_eval_count | [`45-token_accuracy_prd.md`](45-token_accuracy_prd.md) / [`45-token_accuracy_prp.md`](45-token_accuracy_prp.md) | ✅ Completado |
| Deployment Maturity — Health checks, secrets, image versioning, CI/CD | [`46-deployment_maturity_prd.md`](46-deployment_maturity_prd.md) / [`46-deployment_maturity_prp.md`](46-deployment_maturity_prp.md) | ✅ Completado |
| Operational Automation — Data-driven triggers, metric alerts, self-healing | [`47-operational_automation_prd.md`](47-operational_automation_prd.md) / [`47-operational_automation_prp.md`](47-operational_automation_prp.md) | ✅ Completado |

## Convenciones

- Crear el exec plan **antes** de implementar una feature compleja (>3 archivos afectados)
- El plan es un artefacto de primera clase: documenta decisiones, no solo pasos
- Incluir siempre: objetivo, archivos a modificar, schema de datos, orden de implementación
- Marcar el estado al terminar: 📋 Pendiente → 🚧 En progreso → ✅ Completado

## Template de PRD (Product Requirements Document)

Usar este como `docs/exec-plans/<numero>-<nombre>_prd.md` para asentar propósitos e intención.

```markdown
# PRD: [Nombre de la Feature]

## Objetivo y Contexto
[Qué problema resuelve esta implementación y por qué es importante]

## Alcance (In Scope & Out of Scope)
- **In Scope:** [Lista...]
- **Out of Scope:** [Lista...]

## Casos de Uso Críticos
1. [Escenario 1]
2. [Escenario 2]

## Restricciones Arquitectónicas / Requerimientos Técnicos
- [Dependencias o frameworks que no deben evadirse]
- [Criterios de seguridad]
```

## Template de PRP (Product Requirements Plan)

Usar este como `docs/exec-plans/<numero>-<nombre>_prp.md` para asentar ejecución técnica y checkboxes. **OBLIGATORIO: MARCAR LOS CHECKS DURANTE LA EJECUCIÓN.**

```markdown
# PRP: [Nombre de la Feature]

## Archivos a Modificar
- `ruta/al/archivo1.py`: [Qué se cambia]
- `ruta/al/archivo2.md`: [Nuevo archivo]

## Fases de Implementación (con Checkboxes)

### Phase 1: Fundamentos
- [ ] Implementar X
- [ ] Escribir tests para X

### Phase 2: Integración
- [ ] Agregar Y al webhook router
- [ ] Escribir validaciones de borde de Y

### Phase 3: Documentación
- [ ] Correr `make check`
- [ ] Escribir `docs/features/...`
- [ ] Escribir `docs/testing/...`
```
