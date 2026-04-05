# Eval, Guardrails & Observability — Audit Completo

> **Fecha**: 2026-04-04
> **Objetivo**: Mapear todo lo que se mide, lo que no, y qué benchmarks faltan.

---

## 1. Estado Actual: Qué Tenemos

### 1.1 Guardrails Pipeline (`app/guardrails/`)

8 checks que corren post-LLM, pre-envío al usuario:

| # | Check | Tipo | Siempre activo | Resultado persistido |
|---|-------|------|:--------------:|:-------------------:|
| 1 | `check_not_empty` | Sync/Deterministico | ✅ | ✅ `trace_scores` (per-check) |
| 2 | `check_excessive_length` | Sync/Deterministico | ✅ | ✅ `trace_scores` (per-check) |
| 3 | `check_no_raw_tool_json` | Sync/Regex | ✅ | ✅ `trace_scores` (per-check) |
| 4 | `check_language_match` | Async/langdetect | ✅ (config) | ✅ `trace_scores` (per-check) |
| 5 | `check_no_pii` | Sync/Regex | ✅ (config) | ✅ `trace_scores` (per-check) |
| 6 | `check_tool_coherence` | Async/LLM | ❌ Off por default | N/A (no corre) |
| 7 | `check_hallucination` | Async/LLM | ❌ Off por default | N/A (no corre) |
| 8 | `check_code_security` | Sync/Regex | ✅ En write/patch | ❌ Warning al LLM, no persistido |

**Nota**: Los checks 1-5 ya persisten scores individuales por check name en `trace_scores` (`router.py:1711-1717`). El diagnóstico por check ES posible via queries SQL. Los checks 6 y 7 (coherence + hallucination, los más valiosos) están deshabilitados por default.

### 1.2 Tracing (`app/tracing/`)

| Tabla | Qué guarda | Persistido |
|-------|-----------|:----------:|
| `traces` | Input, output, status, timestamps por interacción | ✅ SQLite |
| `trace_spans` | Operaciones internas (LLM, tools, guardrails) con latencia y tokens | ✅ SQLite |
| `trace_scores` | Scores 0-1 por criterio (system, user, llm_judge) | ✅ SQLite |

Langfuse v3 opcional — sincroniza traces, spans, scores, datasets si keys configuradas.

### 1.3 Eval Dataset (`app/eval/`)

| Componente | Qué hace | Persistido |
|------------|----------|:----------:|
| `maybe_curate_to_dataset` | Auto-curación 3-tier post-trace | ✅ `eval_dataset` + tags |
| `add_correction_pair` | Guarda corrección del usuario (bad → expected) | ✅ `eval_dataset` |
| `judge_response` | 4 criterios LLM: correctness, completeness, conciseness, tool_usage | ⚠️ Via caller |
| `export_to_jsonl` | Exporta a JSONL para eval offline | ✅ Archivo |

### 1.4 Regression Eval Suite (`scripts/`)

| Script | Qué hace | Secciones | Exit code CI |
|--------|----------|-----------|:------------:|
| `seed_eval_dataset.py` | 82 golden cases en 16 secciones | chat, math, time, weather, search, notes, projects, selfcode, github, tools, expand, evaluation, automation, knowledge, multicategory, edge | N/A |
| `run_eval.py` | 3 niveles de eval | classify, tools, e2e | ✅ 0/1 |
| `dashboard.py` | HTML con Chart.js | guardrails, latency, dataset, agent | N/A |
| `baseline.py` | Snapshot JSON de métricas | all | N/A |

### 1.5 Métricas de Observability (eval_tools.py — 13 tools desde WhatsApp)

| Tool | Qué mide |
|------|----------|
| `get_eval_summary` | Resumen de traces/scores (7d) |
| `list_recent_failures` | Traces con score < 0.5 |
| `diagnose_trace` | Deep-dive: spans, scores, I/O completo |
| `propose_correction` | Guarda par corrección |
| `add_to_dataset` | Curación manual |
| `get_dataset_stats` | Composición: golden/failure/correction |
| `run_quick_eval` | Eval online con judge 4 criterios |
| `get_latency_stats` | p50/p95/p99 por span |
| `get_search_stats` | Semantic search hit rate |
| `get_agent_stats` | Tool efficiency, tokens, context quality, planner, HITL |
| `get_dashboard_stats` | Failure trend + score distribution |
| `propose_prompt_change` | Draft de modificación de prompt |

### 1.6 Security (`app/security/`)

| Componente | Persistido | Siempre activo |
|------------|:----------:|:--------------:|
| `PolicyEngine` (YAML rules → allow/block/flag) | ❌ Decisión va al audit | ✅ En tool calls |
| `AuditTrail` (append-only JSONL + SHA-256 chain) | ✅ Archivo | ✅ En blocked/flagged |
| `_scrubbed_env` (credential removal de subprocesses) | ❌ Efímero | ✅ Siempre |
| `check_code_security` (patterns peligrosos en código) | ❌ Warning | ✅ En write/patch |

---

## 2. Mapa: Trackeado vs No Trackeado

### ✅ Trackeado y Persistido

| Métrica | Dónde | Granularidad |
|---------|-------|-------------|
| Latencia e2e | `traces` | Per-trace |
| Latencia por operación | `trace_spans` | Per-span |
| Tokens input/output LLM | `trace_spans.metadata` (OTel) | Per-generation |
| Guardrail pass/fail (binario agregado) | `trace_scores` | Per-trace |
| Eval scores (4 criterios) | `trace_scores` | Per-eval-run |
| Dataset calidad (golden/failure/correction) | `eval_dataset` | Auto-curated |
| Security decisions | `audit_trail.jsonl` | Per-tool-call |
| Tool calls y resultados | `trace_spans` (kind=tool) | Per-tool |
| Search hit rates | `trace_spans.metadata` | Per-search |
| Context fill rate | `trace_scores` | Per-trace |
| Goal completion (agent) | `trace_scores` | Per-agent-session |
| HITL escalations | `trace_scores` | Per-tool-call |
| Token consumption | Aggregated from spans | Per-period |

### ❌ No Trackeado — Gaps Identificados

| Gap | Impacto | Esfuerzo | Prioridad |
|-----|---------|----------|-----------|
| **Checks LLM deshabilitados** (`tool_coherence`, `hallucination`) | Las 2 validaciones más valiosas nunca corren | Bajo (solo `.env`) | 🔴 Alta |
| **Code security detections** no persistidas | No hay métricas de código inseguro escrito por el agent | Bajo | 🟡 Media |
| **User satisfaction signals** (no hay 👍/👎 desde WhatsApp) | Tier "golden confirmed" del eval casi nunca se activa | Medio | 🟡 Media |
| **Token cost/budget** (tokens × pricing por modelo) | No se sabe cuánto cuesta cada sesión | Medio | 🟡 Media |
| **Dream/Session Memory effectiveness** | No hay scoring de si memorias extraídas son útiles | Alto | 🟡 Media |
| **Prompt version A/B comparison** | Framework existe (`propose_prompt_change`) pero sin tracking pre/post | Medio | 🟡 Media |
| **Regression eval automático** (scheduled) | `run_eval.py` es manual, no corre periódicamente | Medio | 🟡 Media |
| **PII detecciones desglosadas** (qué tipo: token/email/phone) | No hay métricas de qué PII se filtra más | Bajo | 🟢 Baja |
| **Error rates pre-agregados por tool** | Disponible on-demand via `get_agent_stats`, no pre-computado | Bajo | 🟢 Baja |
| **Latency alerting/thresholds** | Se pueden consultar percentiles pero no hay alertas | Medio | 🟢 Baja |

---

## 3. Benchmarks: Qué Tenemos vs Qué Falta

### ✅ Benchmarks Existentes

| Benchmark | Script | Qué evalúa | Cases | CI-compatible |
|-----------|--------|------------|:-----:|:-------------:|
| **Intent Classification** | `run_eval.py --mode classify` | `classify_intent()` recall | 82 | ✅ exit code |
| **Tool Selection** | `run_eval.py --mode tools` | classify + `select_tools()` | 82 | ✅ exit code |
| **E2E LLM-as-Judge** | `run_eval.py --mode e2e` | Respuesta completa vs expected | 82 | ✅ exit code |
| **Guardrails Regression** | `run_eval.py --mode guardrails` | Checks deterministicos sobre responses | 82 | ✅ exit code |
| **QAG Multi-Criteria Judge** | `judge_response()` (judge.py) | correctness, completeness, conciseness, tool_usage | On-demand | ❌ |
| **Guardrail Pass Rate** | `get_dashboard_stats` | % checks passed over time | Continuo | ❌ (WhatsApp only) |
| **Latency Percentiles** | `get_latency_stats` | p50/p95/p99 por operación | Continuo | ❌ |

**Coverage del seed dataset (82 cases)**:

```
chat(5) math(8) time(8) weather(4) search(4) notes(7) projects(12)
selfcode(7) github(3) tools(3) expand(3) evaluation(4) automation(3)
knowledge(2) multicategory(5) edge(4)
```

### ❌ Benchmarks Faltantes

| Benchmark propuesto | Qué mediría | Prioridad | Esfuerzo |
|---------------------|-------------|-----------|----------|
| **Memory Retrieval Quality** | Dado un query, ¿las memorias recuperadas son relevantes? Precision@K | 🔴 Alta | Medio — necesita golden set de query→expected_memories |
| **Agent Plan Quality** | Dado un objetivo, ¿el plan generado es razonable? LLM-as-judge | 🟡 Media | Medio — dataset de objectives→expected_plans |
| **Context Saturation** | ¿Qué pasa con la calidad de respuesta cuando context fill > 80%? Correlación | 🟡 Media | Bajo — ya hay datos, solo falta el análisis |
| **Language Consistency** | % de respuestas en el idioma correcto sobre N interacciones consecutivas | 🟡 Media | Bajo — ya existe `check_language_match`, solo falta benchmark dedicado |
| **Tool Hallucination Rate** | % de veces que el LLM inventa un tool que no existe | 🟡 Media | Bajo — ya trackeable via tool errors en spans |
| **Remediation Effectiveness** | Cuando guardrail falla y se re-genera, ¿la 2da respuesta pasa? | 🟡 Media | Medio — necesita trackear retry success rate |
| **Code Security False Positive Rate** | De las detecciones de `check_code_security`, cuántas son false positives | 🟢 Baja | Alto — requiere revisión manual |
| **Dream Consolidation Quality** | Después del dream, ¿las memorias son más coherentes? Before/after diff | 🟢 Baja | Alto — subjetivo, necesita human eval |
| **Session Memory Precision** | De los facts extraídos por session memory, ¿cuántos son correctos? | 🟢 Baja | Alto — requiere human eval |
| **Subagent vs Direct Execution** | ¿El subagent mejora la calidad de tasks complejas? A/B | 🟢 Baja | Alto — necesita A/B framework |

---

## 4. Recomendaciones Priorizadas

### Tier 1: Quick Wins (< 1 día cada uno)

**1. Habilitar `guardrails_llm_checks=True`**
`check_tool_coherence` y `check_hallucination` ya están implementados. Solo requiere `.env` change + testing de latencia (timeout 3s). Estos son los checks más valiosos del pipeline.

**2. ✅ Persistir code_security warnings** (implementado)
`_security_warning()` en `selfcode_tools.py` ahora registra `trace.add_score("code_security_warning", 0.0)` con detalle del patrón detectado. Queryable desde `trace_scores`.

**3. ✅ Benchmark de guardrails en run_eval.py** (implementado)
Nuevo modo `--mode guardrails` que re-ejecuta los checks deterministicos sobre responses del dataset. No requiere LLM, <1s. CI-compatible: `make eval-guardrails` (threshold 90%).

> **Nota**: Los guardrail scores granulares POR CHECK ya existen (`router.py:1711-1717`). Cada check persiste su score individual a `trace_scores` con `name=check_name`.

### Tier 2: Medium Effort (1-3 días)

**5. User feedback via WhatsApp reactions**
Mapear 👍/👎 reactions a `trace.add_score("user_satisfaction", 1.0/0.0)`. Esto activa el tier "golden confirmed" del eval que hoy está dormido.

**6. Scheduled regression eval**
APScheduler job que corre `run_eval.py --mode classify` diariamente. Persiste accuracy como score. Alerta si baja del threshold.

**7. Memory retrieval benchmark**
Golden set de 20-30 queries con expected memories. Mide Precision@5 y Recall. Integra como `--mode memory` en `run_eval.py`.

**8. Token cost tracking**
Multiplicar tokens × pricing por modelo en `finish_trace`. Persiste como `trace_score("estimated_cost")`. Agrega `get_cost_stats` a eval_tools.

### Tier 3: Strategic (1+ semanas)

**9. Prompt A/B testing framework**
Extender `propose_prompt_change` para hacer split testing: N% de requests usan prompt nuevo, trackear scores comparativos.

**10. Context saturation analysis**
Script que correlaciona `context_fill_rate` > 0.8 con guardrail failures y judge scores. Identifica el punto de quiebre.

**11. End-to-end agent benchmark**
Dataset de 10-15 agent objectives complejos con expected outcomes. Evalúa plan quality + execution + delivery.

---

## 5. Resumen Ejecutivo

| Dimensión | Estado | Nota |
|-----------|--------|------|
| **Guardrails** | ⚠️ Parcial | 5/8 checks activos con scores per-check, LLM checks (6-7) off |
| **Tracing** | ✅ Robusto | SQLite + Langfuse opcional, spans, scores, tokens OTel |
| **Eval Dataset** | ✅ Funcional | 3-tier auto-curation, 82 seed cases, export JSONL |
| **Benchmarks** | ⚠️ Parcial | 3 modos (classify/tools/e2e), CI-compatible, pero no scheduled |
| **Security Audit** | ✅ Robusto | Policy engine + audit trail hash chain + credential scrub |
| **Observability** | ✅ Completo | 13 tools WhatsApp, dashboard HTML, latency stats |
| **User Signals** | ❌ Ausente | No hay mecanismo de feedback desde WhatsApp |
| **Memory Quality** | ❌ Sin medir | Dream y session memory no tienen eval de effectiveness |

**Las 3 acciones de mayor impacto/esfuerzo**:
1. Guardrail scores granulares (1h de código, desbloquea diagnóstico de problemas)
2. Habilitar LLM checks (5 min de config, agrega coherence + hallucination detection)
3. User feedback via reactions (medio día, activa el tier más valioso del eval)
