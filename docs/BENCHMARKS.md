# Benchmarks & Eval — Source Reference

> **Última actualización**: 2026-04-04
> **Audit completo**: [`docs/EVAL_GUARDRAILS_AUDIT.md`](EVAL_GUARDRAILS_AUDIT.md)
> **Exec Plans**: [Plan 61](exec-plans/61-guardrails_eval_hardening_prd.md) (guardrails hardening), [Plan 62](exec-plans/62-benchmark_suite_prd.md) (benchmark expansion)

---

## 1. Regression Eval Suite

### Scripts

| Script | Rol | Líneas clave |
|--------|-----|-------------|
| [`scripts/run_eval.py`](../scripts/run_eval.py) | Runner de benchmarks, 4 modos | `:701` `_run_eval()` — orchestrator principal |
| [`scripts/seed_eval_dataset.py`](../scripts/seed_eval_dataset.py) | 82 golden cases en 16 secciones | `:47` `CASES` list, `:33` `EvalCase` dataclass |
| [`scripts/dashboard.py`](../scripts/dashboard.py) | HTML con Chart.js | Genera reporte visual offline |
| [`scripts/baseline.py`](../scripts/baseline.py) | Snapshot JSON de métricas | Incluye agent metrics (Plan 39) |

### Modos de Evaluación

| Modo | Función | Línea | Qué evalúa | LLM calls | Threshold |
|------|---------|-------|-------------|:---------:|:---------:|
| `classify` | `_run_classify()` | [`run_eval.py:386`](../scripts/run_eval.py#L386) | `classify_intent()` recall-based | 1 per entry | 80% |
| `tools` | `_run_tools()` | [`run_eval.py:428`](../scripts/run_eval.py#L428) | classify + `select_tools()` | 1 per entry | 70% |
| `e2e` | `_run_e2e()` | [`run_eval.py:539`](../scripts/run_eval.py#L539) | Respuesta completa + LLM-as-judge | 2 per entry | 50% |
| `guardrails` | `_run_guardrails()` | [`run_eval.py:484`](../scripts/run_eval.py#L484) | Checks deterministicos sobre responses | 0 | 90% |

### Scoring Functions

| Función | Línea | Lógica |
|---------|-------|--------|
| `_score_categories()` | [`run_eval.py:132`](../scripts/run_eval.py#L132) | Recall: `len(expected ∩ actual) / len(expected)` |
| `_score_tools()` | [`run_eval.py:148`](../scripts/run_eval.py#L148) | `len(expected ∩ selected) / len(expected)` |
| `_judge_response()` | [`run_eval.py:238`](../scripts/run_eval.py#L238) | QAG multi-criteria: correctness, completeness, tool_usage |
| `_parse_judge_response()` | [`run_eval.py:170`](../scripts/run_eval.py#L170) | Parsea YES/NO/PASS/FAIL con fallback regex |

### Seed Dataset (82 cases)

Definidos en [`scripts/seed_eval_dataset.py:47`](../scripts/seed_eval_dataset.py#L47):

| Sección | Cases | eval_types | Qué testea |
|---------|:-----:|------------|-----------|
| `chat` | 5 | classify, e2e | Conversación sin tools |
| `math` | 8 | classify, tools, e2e | Calculator tool |
| `time` | 8 | classify, tools, e2e | DateTime + reminders |
| `weather` | 4 | classify, tools, e2e | Weather API |
| `search` | 4 | classify, tools, e2e | Web search |
| `notes` | 7 | classify, tools, e2e | Notes CRUD |
| `projects` | 12 | classify, tools, e2e | Project management |
| `selfcode` | 7 | classify, tools, e2e | Code introspection |
| `github` | 3 | classify, tools | GitHub MCP |
| `tools` | 3 | classify, tools, e2e | Meta-tools discovery |
| `expand` | 3 | classify, tools | MCP registry |
| `evaluation` | 4 | classify, tools | Eval pipeline self-tools |
| `automation` | 3 | classify, tools | Operational automation |
| `knowledge` | 2 | classify, tools | Knowledge graph |
| `multicategory` | 5 | classify, tools | Multi-tool coordination |
| `edge` | 4 | classify | URLs, idiomas raros, multi-request |

### Makefile Targets

```
make eval-seed           # Poblar dataset (idempotente)
make eval-seed-clear     # Limpiar + re-seedear
make eval-classify       # Level 1 (threshold 0.8)
make eval-tools          # Level 2 (threshold 0.7)
make eval-e2e            # Level 3 (threshold 0.5)
make eval-e2e-verbose    # Level 3 con detalle per-entry
make eval-guardrails     # Level G (threshold 0.9, sin LLM)
make eval-langfuse       # Level 3 + sync a Langfuse
make eval                # Pipeline: seed + classify + e2e
```

Definidos en [`Makefile:28-52`](../Makefile#L28).

---

## 2. Guardrails Pipeline

### Pipeline

| Archivo | Función | Línea | Rol |
|---------|---------|-------|-----|
| [`app/guardrails/pipeline.py`](../app/guardrails/pipeline.py) | `run_guardrails()` | `:21` | Orquesta checks, retorna `GuardrailReport` |
| [`app/guardrails/models.py`](../app/guardrails/models.py) | `GuardrailResult` | `:4` | Resultado por check (passed, check_name, details, latency_ms) |
| [`app/guardrails/models.py`](../app/guardrails/models.py) | `GuardrailReport` | `:11` | Agregado: passed, results[], total_latency_ms |

### Checks Individuales

Todos en [`app/guardrails/checks.py`](../app/guardrails/checks.py):

| Check | Línea | Tipo | Siempre on | Qué valida |
|-------|-------|------|:----------:|-----------|
| `check_not_empty` | `:32` | Sync | ✅ | Reply no vacío |
| `check_language_match` | `:44` | Async/langdetect | ✅ (config) | Idioma reply = idioma user (skip si <30 chars) |
| `check_no_pii` | `:138` | Sync/Regex | ✅ (config) | No tokens/emails/phones/DNI en reply |
| `check_excessive_length` | `:190` | Sync | ✅ | Reply <8000 chars |
| `check_no_raw_tool_json` | `:205` | Sync/Regex | ✅ | No `{"tool_call"` leakeado |
| `check_tool_coherence` | `:218` | Async/LLM | ❌ Off | Reply coherente con pregunta (Ollama, timeout 3s) |
| `check_hallucination` | `:253` | Async/LLM | ❌ Off | No datos inventados (Ollama, timeout 3s) |
| `check_code_security` | `:314` | Sync/Regex | ✅ (en write/patch) | 11 patrones: eval, exec, pickle, SQL injection, XSS |

### PII Patterns

Definidos en [`app/guardrails/checks.py:12-23`](../app/guardrails/checks.py#L12):

| Pattern | Regex | Ejemplo |
|---------|-------|---------|
| `_RE_DNI` | `\b\d{7,8}\b` | DNI argentino |
| `_RE_TOKEN` | `Bearer\|sk-\|whsec_` | API tokens |
| `_RE_EMAIL` | Standard email regex | user@domain.com |
| `_RE_PHONE` | `\+?[\d\s\-]{10,15}` | +54 11 1234-5678 |

### Code Security Patterns

Definidos en [`app/guardrails/checks.py:314+`](../app/guardrails/checks.py#L314). 11 patterns con recomendación por patrón:

| Pattern | Lenguajes | Recomendación |
|---------|-----------|---------------|
| `eval()` | Python | `ast.literal_eval()` |
| `exec()` | Python | Parser dedicado |
| `os.system()` | Python | `subprocess.run()` |
| `os.popen()` | Python | `subprocess.run()` |
| `subprocess.*shell=True` | Python | `shell=False` + lista |
| `pickle.load` | Python | JSON/msgpack |
| `yaml.load` sin Loader | Python | `yaml.safe_load()` |
| `.innerHTML =` | JS | `textContent` o sanitize |
| `document.write` | JS | DOM methods |
| `dangerouslySetInnerHTML` | React | Sanitize con DOMPurify |
| `new Function(` | JS | Evitar eval dinámico |

### Integración en Router

Score recording: [`app/webhook/router.py:1711-1717`](../app/webhook/router.py#L1711)
```python
for gr in guardrail_report.results:
    await trace_ctx.add_score(name=gr.check_name, value=1.0 if gr.passed else 0.0, source="system")
```

Remediation on failure: [`app/webhook/router.py:1701-1708`](../app/webhook/router.py#L1701)

Code security trace score: [`app/skills/tools/selfcode_tools.py:47-64`](../app/skills/tools/selfcode_tools.py#L47) — persiste `code_security_warning` via `get_current_trace()`

### Config

Definidas en [`app/config.py`](../app/config.py):

| Setting | Default | Efecto |
|---------|---------|--------|
| `guardrails_enabled` | `True` | Pipeline on/off |
| `guardrails_language_check` | `True` | Check de idioma |
| `guardrails_default_language` | `"es"` | Fallback si user_text < 30 chars |
| `guardrails_pii_check` | `True` | Check PII |
| `guardrails_llm_checks` | `False` | LLM judges (coherence + hallucination) |
| `guardrails_llm_timeout` | `3.0` | Timeout en segundos para LLM judges |

---

## 3. Eval Dataset & Curation

### Auto-Curation

| Archivo | Función | Línea | Rol |
|---------|---------|-------|-----|
| [`app/eval/dataset.py`](../app/eval/dataset.py) | `maybe_curate_to_dataset()` | `:18` | 3-tier auto-curation post-trace |
| [`app/eval/dataset.py`](../app/eval/dataset.py) | `add_correction_pair()` | `:116` | Guarda corrección del usuario |

### 3-Tier Logic

| Tier | Condición | Prioridad |
|------|-----------|:---------:|
| `failure` | Cualquier system score < 0.3 O user score < 0.3 | 1 (más alta) |
| `golden` confirmed | Todos system scores >= 0.8 Y algún user score >= 0.8 | 2 |
| `golden` candidate | Todos system scores >= 0.8, sin user scores | 3 |

### LLM-as-Judge

| Archivo | Función | Línea | Rol |
|---------|---------|-------|-----|
| [`app/eval/judge.py`](../app/eval/judge.py) | `judge_response()` | `:66` | 4 criterios: correctness, completeness, conciseness, tool_usage |
| [`app/eval/judge.py`](../app/eval/judge.py) | `JudgeResult` | `:36` | Scores 0-1, average, passed (avg >= 0.6 AND all >= 0.3) |
| [`app/eval/exporter.py`](../app/eval/exporter.py) | `export_to_jsonl()` | `:12` | Exporta dataset a JSONL |

### DB Schema

Definido en [`app/database/db.py:236-255`](../app/database/db.py#L236):

```sql
eval_dataset (id, trace_id, entry_type, input_text, output_text, expected_output, metadata, created_at)
eval_dataset_tags (dataset_id, tag)   -- PK(dataset_id, tag)
```

---

## 4. Tracing & Observability

### Core

| Archivo | Clase/Función | Línea | Rol |
|---------|--------------|-------|-----|
| [`app/tracing/context.py`](../app/tracing/context.py) | `TraceContext` | `:58` | Async context manager, contextvars propagation |
| [`app/tracing/context.py`](../app/tracing/context.py) | `SpanData` | `:32` | Span con input/output/metadata/latency |
| [`app/tracing/context.py`](../app/tracing/context.py) | `get_current_trace()` | `:27` | Acceso global via contextvar |
| [`app/tracing/recorder.py`](../app/tracing/recorder.py) | `TraceRecorder` | `:15` | Persistencia SQLite + Langfuse (best-effort) |

### DB Schema

Definido en [`app/database/db.py:150-199`](../app/database/db.py#L150):

```sql
traces       (id, phone_number, input_text, output_text, wa_message_id, message_type, status, started_at, completed_at, metadata)
trace_spans  (id, trace_id, parent_id, name, kind, input, output, status, started_at, completed_at, latency_ms, metadata)
trace_scores (id, trace_id, span_id, name, value REAL, source, comment, created_at)
```

Span kinds: `span`, `generation`, `tool`, `guardrail`
Score sources: `system`, `user`, `llm_judge`, `human`

### Scores Persistidos Automáticamente

| Score name | Value | Source | Dónde se registra |
|-----------|-------|--------|------------------|
| `not_empty` | 0/1 | system | `router.py:1713` — per guardrail check |
| `language_match` | 0/1 | system | `router.py:1713` |
| `no_pii` | 0/1 | system | `router.py:1713` |
| `excessive_length` | 0/1 | system | `router.py:1713` |
| `no_raw_tool_json` | 0/1 | system | `router.py:1713` |
| `code_security_warning` | 0.0 | system | `selfcode_tools.py:57` — solo cuando pattern detectado |
| `context_fill_rate` | 0-1 | system | `router.py` — estimated_tokens / context_limit |
| `classify_upgrade` | 0/1 | system | `router.py` — re-classified with context |
| `goal_completion` | 0/1 | llm_judge | `agent/loop.py` — background, post-agent |
| `hitl_escalation` | 0/1 | system | `executor.py` — HITL callback invoked |
| `correctness` | 0-1 | llm_judge | `eval_tools.py` — run_quick_eval |
| `completeness` | 0-1 | llm_judge | `eval_tools.py` — run_quick_eval |
| `conciseness` | 0-1 | llm_judge | `eval_tools.py` — run_quick_eval |
| `tool_usage` | 0-1 | llm_judge | `eval_tools.py` — run_quick_eval |

---

## 5. Métricas Repository

Funciones de query en [`app/database/repository.py`](../app/database/repository.py):

| Función | Línea | Qué retorna |
|---------|-------|-------------|
| `get_eval_summary()` | `:1378` | Total traces, completed, failed, avg/min/max per metric |
| `get_failure_trend()` | `:1496` | Daily total/failed/pass_rate (30d) |
| `get_score_distribution()` | `:1514` | Avg score + failure count per check name |
| `get_latency_percentiles()` | `:1714` | p50/p95/p99 per span name |
| `get_dataset_entries()` | `:1160` | Dataset entries filtradas por type/tag/limit |
| `get_tool_efficiency()` | `:1846` | Avg/max tool calls, LLM iterations, error rates per tool |
| `get_tool_redundancy()` | `:1947` | Same tool+args called >1x en misma traza |
| `get_token_consumption()` | `:1919` | Avg input/output tokens, total, n_generations |
| `get_context_quality_metrics()` | `:1965` | Avg fill rate, near_limit count |
| `get_context_rot_risk()` | `:2029` | Fill rate vs guardrail pass rate correlation |
| `get_planner_metrics()` | `:2072` | Planner sessions, replan rate, avg replans |
| `get_hitl_rate()` | `:2120` | Total HITL, approved, rejected |

---

## 6. WhatsApp Eval Tools

13 tools accesibles desde WhatsApp, registrados en [`app/skills/tools/eval_tools.py`](../app/skills/tools/eval_tools.py):

| Tool | Línea | Qué hace |
|------|-------|----------|
| `get_eval_summary` | `:36` | Resumen traces/scores (7d) |
| `list_recent_failures` | `:62` | Traces con score < 0.5 |
| `diagnose_trace` | `:82` | Deep-dive: spans, scores, I/O |
| `propose_correction` | `:120` | Guardar par corrección |
| `add_to_dataset` | `:141` | Curación manual (golden/failure) |
| `get_dataset_stats` | `:165` | Composición del dataset |
| `run_quick_eval` | `:187` | Eval online con judge 4 criterios |
| `get_latency_stats` | `:313` | p50/p95/p99 por span |
| `get_search_stats` | `:355` | Semantic search hit rate |
| `get_agent_stats` | `:379` | Tool efficiency, tokens, context, planner |
| `get_dashboard_stats` | `:501` | Failure trend + score distribution |
| `propose_prompt_change` | `:538` | Draft prompt modification |

---

## 7. Security Eval

| Archivo | Clase/Función | Línea | Rol |
|---------|--------------|-------|-----|
| [`app/security/policy_engine.py`](../app/security/policy_engine.py) | `PolicyEngine` | `:12` | Evalúa tool calls vs YAML rules |
| [`app/security/policy_engine.py`](../app/security/policy_engine.py) | `evaluate()` | método | First-match → allow/block/flag |
| [`app/security/audit.py`](../app/security/audit.py) | `AuditTrail` | `:26` | Append-only JSONL + SHA-256 hash chain |
| [`app/security/audit.py`](../app/security/audit.py) | `record()` | método | Persiste entry con hash del anterior |
| [`app/security/models.py`](../app/security/models.py) | `PolicyDecision` | `:28` | action, reason, rule_id, is_allowed/is_blocked |
| [`app/security/exceptions.py`](../app/security/exceptions.py) | `HitlRequiredException` | — | Raised cuando policy action=FLAG |

Policy file: `data/security_policies.yaml` (YAML safe_load, fail-secure si no existe).
Audit log: `data/audit_trail.jsonl` (append-only, thread-safe via Lock).

---

## 8. Config Settings (Eval & Quality)

Todas en [`app/config.py`](../app/config.py):

| Setting | Default | Categoría |
|---------|---------|-----------|
| `guardrails_enabled` | `True` | Guardrails |
| `guardrails_language_check` | `True` | Guardrails |
| `guardrails_default_language` | `"es"` | Guardrails |
| `guardrails_pii_check` | `True` | Guardrails |
| `guardrails_llm_checks` | `False` | Guardrails |
| `guardrails_llm_timeout` | `3.0` | Guardrails |
| `tracing_sample_rate` | `1.0` | Tracing |
| `trace_retention_days` | `90` | Tracing |
| `eval_auto_curate` | `True` | Eval |
| `context_window_tokens` | `32768` | Context |
| `memory_similarity_threshold` | `1.0` | Memory |
| `semantic_search_top_k` | `5` | Memory |

---

## 9. Queries SQL Útiles

```sql
-- Pass rate por guardrail check (últimos 7 días)
SELECT name, ROUND(AVG(value) * 100, 1) AS pass_rate, COUNT(*) AS n
FROM trace_scores
WHERE source = 'system' AND created_at > datetime('now', '-7 days')
  AND name IN ('not_empty', 'language_match', 'no_pii', 'excessive_length', 'no_raw_tool_json')
GROUP BY name ORDER BY pass_rate ASC;

-- Code security warnings
SELECT comment, COUNT(*) AS n
FROM trace_scores
WHERE name = 'code_security_warning'
GROUP BY comment ORDER BY n DESC;

-- Latencia p95 por span (últimos 7 días)
SELECT name, COUNT(*) AS n,
       ROUND(AVG(latency_ms)) AS avg_ms
FROM trace_spans
WHERE completed_at > datetime('now', '-7 days')
GROUP BY name ORDER BY avg_ms DESC;

-- Dataset composition
SELECT entry_type, COUNT(*) AS n FROM eval_dataset GROUP BY entry_type;

-- Top failing guardrail tags
SELECT tag, COUNT(*) AS n
FROM eval_dataset_tags
WHERE tag LIKE 'guardrail:%'
GROUP BY tag ORDER BY n DESC;

-- Context fill rate distribution
SELECT
  CASE
    WHEN value < 0.5 THEN '0-50%'
    WHEN value < 0.8 THEN '50-80%'
    WHEN value < 0.9 THEN '80-90%'
    ELSE '90-100%'
  END AS bucket,
  COUNT(*) AS n
FROM trace_scores
WHERE name = 'context_fill_rate'
GROUP BY bucket;
```
