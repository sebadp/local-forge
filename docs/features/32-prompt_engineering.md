# Feature: Prompt Engineering & Versioning

> **Versión**: v1.0
> **Fecha de implementación**: 2026-03-01
> **Exec plan**: [`docs/exec-plans/32-prompt_engineering_prd.md`](../exec-plans/32-prompt_engineering_prd.md)
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

Convierte los 28 prompts del sistema en artefactos versionables de primera clase: se guardan en DB con número de versión, se pueden proponer mejoras con `/approve-prompt`, y cada cambio puede correr una eval suite antes de activarse. Incluye correcciones técnicas (latencia de chain-of-thought, few-shot en el classifier, limpieza de filler text) y un comando `/prompts` para inspeccionar el catálogo completo.

---

## Arquitectura

```
startup
  │
  ▼
prompt_registry.py (PROMPT_DEFAULTS)
  │  seed_default_prompts() — idempotente
  ▼
prompt_versions (SQLite)
  │
  ├── get_active_prompt()          ← cache → DB → registry → default → ValueError
  │     ↑ invalidate_prompt_cache() on /approve-prompt
  │
  ├── activate_with_eval()         ← LLM-as-judge sobre eval_dataset
  │     └─► retorna score sin activar (advisory)
  │
  └── /approve-prompt <name> <v>  ← cmd_approve_prompt
        ├── corre activate_with_eval() (si hay ollama_client)
        ├── muestra ✅/⚠️ score
        └── activa igual (eval es informativo)

/prompts [name] [version]         ← cmd_prompts
  ├── sin args  → list_all_active_prompts()
  ├── <name>    → get_active_prompt_version() + list_prompt_versions()
  └── <name> <v>→ get_prompt_version()
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/eval/prompt_registry.py` | Catálogo centralizado — `PROMPT_DEFAULTS` con 9 prompts inline |
| `app/eval/prompt_manager.py` | `get_active_prompt()` (fallback chain + cache), `activate_with_eval()`, `invalidate_prompt_cache()` |
| `app/database/repository.py` | `seed_default_prompts()`, `list_all_active_prompts()`, `list_prompt_versions()`, `get_prompt_version()` |
| `app/commands/builtins.py` | `cmd_approve_prompt` (con eval integrado), `cmd_prompts` (catálogo) |
| `app/skills/router.py` | `classify_intent()` con `repository` opcional — usa prompt versioned de DB |
| `app/conversation/summarizer.py` | `flush_to_memory()` y `maybe_summarize()` usan `get_active_prompt()` |
| `app/memory/consolidator.py` | `consolidate_memories()` usa `get_active_prompt()` |
| `app/agent/planner.py` | `create_plan()`, `replan()`, `synthesize()` usan `get_active_prompt()` |
| `app/formatting/compaction.py` | `think=False` + filler text removido |
| `tests/test_prompt_registry.py` | 19 tests: seed, fallback chain, cache, classify_intent |
| `tests/test_eval_coupled_versioning.py` | 11 tests: activate_with_eval, run_quick_eval con override, /approve-prompt |
| `tests/test_prompt_catalog_command.py` | 11 tests: /prompts en los 3 modos |

---

## Walkthrough técnico: cómo funciona

### 1. Startup — seeding automático

En `main.py` lifespan, si `prompt_versioning_enabled=True`:
```python
await repository.seed_default_prompts(PROMPT_DEFAULTS)
```
`seed_default_prompts()` inserta v1 para cada prompt del catálogo que no tenga versión activa en DB. Es idempotente — restarts seguros.

### 2. Resolución de prompts en runtime

Todo el código usa `get_active_prompt(name, repository)` en vez de constantes:

```
get_active_prompt("classifier", repo)
  → 1. ¿Está en _active_prompts cache?  → retorna
  → 2. DB: get_active_prompt_version()  → guarda en cache, retorna
  → 3. prompt_registry.get_default()    → guarda en cache, retorna
  → 4. param `default` explícito        → guarda en cache, retorna
  → 5. ValueError                       → crash explícito
```

### 3. Proponer y evaluar una nueva versión

```
propose_prompt_change("summarizer", "pierde detalles técnicos", ...)
  → LLM genera contenido mejorado
  → save_prompt_version("summarizer", version=2, created_by="agent")

/approve-prompt summarizer 2
  → activate_with_eval("summarizer", 2, repo, ollama)
      → get_dataset_entries(limit=20)
      → por cada entry: chat(candidate_system + user_input) → judge(yes/no)
      → retorna {"score": 0.85, "passed": True, "activated": False, ...}
  → muestra "✅ Eval score: 85%"
  → activate_prompt_version("summarizer", 2)
  → invalidate_prompt_cache("summarizer")
  → próximas llamadas usan v2
```

### 4. Inspección con `/prompts`

```
/prompts                    → tabla de todos los prompts activos
/prompts classifier         → contenido (600 chars) + historial de versiones
/prompts classifier 2       → contenido v2 específica (800 chars)
```

### 5. Mejoras técnicas (Fase 1)

Todos los prompts binarios/JSON usan `think=False`:
- `check_tool_coherence`, `check_hallucination` (guardrails)
- `flush_to_memory`, `maybe_summarize` (summarizer)
- `consolidate_memories` (consolidator)
- `compact_tool_output` (compaction)
- `propose_prompt_change` (evolution)

El classifier tiene 6 few-shot examples en el template: time, math, notes, search, projects, none.

---

## Cómo extenderla

**Agregar un prompt nuevo al catálogo:**
1. Editar `app/eval/prompt_registry.py` — agregar la constante string y el entry en `PROMPT_DEFAULTS`
2. En el módulo que usa el prompt, reemplazar la constante hardcodeada por `await get_active_prompt("nuevo_nombre", repository, default=CONSTANTE_ANTERIOR)`
3. El próximo restart lo seedea automáticamente en DB

**Mejorar un prompt existente:**
```
# Via agente (el LLM propone):
propose_prompt_change("classifier", "feedback del usuario", "instrucción adicional")

# Via comando directo (contenido manual):
/approve-prompt classifier 2
```

**Deshabilitar el seeding:**
```
PROMPT_VERSIONING_ENABLED=false
```

---

## Guía de testing

→ Ver [`docs/testing/32-prompt_engineering_testing.md`](../testing/32-prompt_engineering_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Catálogo centralizado en `prompt_registry.py` | Defaults inline en cada módulo | Evita circular imports; permite seed en loop; un solo lugar para listar todo |
| `activate_with_eval` es advisory (no blocking) | Bloquear activación si score < threshold | El dataset puede no cubrir el caso de uso del nuevo prompt; el humano decide |
| Cache en memoria con `invalidate_prompt_cache()` | Sin cache (siempre DB) | La resolución del prompt ocurre en cada mensaje; la DB no debe ser el hot path |
| `think=False` explícito en TODO prompt binario/JSON | Depender del default del modelo | qwen3.5:9b genera 500-2000ms de CoT extra por defecto; JSON puede contaminarse |
| `repository: object | None = None` en planner y router | Param requerido | Backward compat — callers sin acceso a repo siguen funcionando |
| Eval usa LLM-as-judge binario (yes/no) | Word overlap / BLEU | Más robusto a variaciones de redacción; mismo patrón que `run_quick_eval` |

---

## Gotchas y edge cases

- **`patch("app.commands.builtins.activate_with_eval", ...)`** requiere que el import sea a nivel de módulo en `builtins.py` (no lazy dentro de la función). Si se mueve de vuelta adentro, los tests de mock fallan.
- **`think=False` con tools**: cuando hay tools en el payload de Ollama, `think` se ignora — no hace falta preocuparse por conflicto. Solo importa en prompts sin tools.
- **Prompt de compaction**: `compact_tool_output()` no tiene acceso a `repository` (es pura transformación de string). El prompt de compaction sigue hardcodeado; el catálogo lo registra para seeding/inspección pero el wiring dinámico queda pendiente.
- **Cache cross-test**: `invalidate_prompt_cache()` debe llamarse en `teardown` de tests que modifiquen prompts activos, o usar nombres de prompt únicos por test.
- **`seed_default_prompts` es conservador**: si ya hay una versión activa (aunque sea v1), no la sobreescribe. Rollback manual requerido si el default se actualiza en código.

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `prompt_versioning_enabled` | `True` | Activa el seeding de defaults en startup |
| `tracing_enabled` | `False` | No afecta el prompt registry, pero `run_quick_eval` requiere que Ollama esté disponible |
