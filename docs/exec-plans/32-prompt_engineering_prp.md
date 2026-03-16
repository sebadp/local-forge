# PRP: Prompt Engineering & Versioning

## Archivos a Modificar

### Fase 1 — Quick Fixes
- `app/guardrails/checks.py`: Agregar `think=False` a `check_tool_coherence` y `check_hallucination`
- `app/conversation/summarizer.py`: Agregar `think=False` a `maybe_summarize()` y `flush_to_memory()`
- `app/memory/consolidator.py`: Agregar `think=False` a `consolidate_memories()`
- `app/formatting/compaction.py`: Agregar `think=False` a LLM compaction; remover instrucción de "full result available"
- `app/eval/evolution.py`: Agregar `think=False` a `propose_prompt_change()`
- `app/skills/router.py`: Agregar few-shot examples al `_CLASSIFIER_PROMPT_TEMPLATE`
- `tests/`: Tests unitarios para cada cambio

### Fase 2 — Prompt Registry
- `app/eval/prompt_manager.py`: Extender con `PromptRegistry` — registro de prompts nombrados con defaults
- `app/eval/prompt_registry.py` (nuevo): Catálogo centralizado de prompt names → default content
- `app/config.py`: Setting `prompt_versioning_enabled: bool = True`
- `app/database/repository.py`: Método `seed_default_prompts()` para insertar v1 si no existe
- `app/database/db.py`: Llamar `seed_default_prompts()` en `init_db()`
- `app/skills/router.py`: Usar `get_active_prompt("classifier", ...)` en vez de constante
- `app/conversation/summarizer.py`: Usar `get_active_prompt("summarizer", ...)` y `get_active_prompt("flush_to_memory", ...)`
- `app/memory/consolidator.py`: Usar `get_active_prompt("consolidator", ...)`
- `app/formatting/compaction.py`: Usar `get_active_prompt("compaction", ...)`
- `app/agent/planner.py`: Usar `get_active_prompt("planner_create", ...)` etc.
- `tests/`: Tests para registry y seeding

### Fase 3 — Eval-Coupled Versioning
- `app/eval/prompt_manager.py`: `activate_with_eval()` — corre eval antes de activar
- `app/commands/builtins.py`: `/approve-prompt` muestra score de eval antes de confirmar
- `app/skills/tools/eval_tools.py`: Extender `run_quick_eval` para aceptar prompt override
- `tests/`: Tests para eval coupling

### Fase 4 — Prompt Catalog Command
- `app/commands/builtins.py`: Comando `/prompts [nombre] [version]`
- `tests/`: Tests para el comando

---

## Fases de Implementación

### Phase 1: Quick Fixes (sin cambios de arquitectura)

- [x] **1.1** `checks.py`: Agregar `think=False` a `check_tool_coherence`
- [x] **1.2** `checks.py`: Agregar `think=False` a `check_hallucination` (mismo patrón)
- [x] **1.3** `summarizer.py`: Agregar `think=False` a la llamada LLM en `maybe_summarize()`
- [x] **1.4** `summarizer.py`: Agregar `think=False` a la llamada LLM en `flush_to_memory()`
- [x] **1.5** `consolidator.py`: Agregar `think=False` a la llamada LLM en `consolidate_memories()`
- [x] **1.6** `compaction.py`: Agregar `think=False` a la llamada LLM en `compact_tool_output()`
- [x] **1.7** `compaction.py`: Remover la línea del prompt que dice `"4. Add a note that a full result is available on request."`
- [x] **1.8** `evolution.py`: Agregar `think=False` a `propose_prompt_change()`
- [x] **1.9** `router.py`: Agregar few-shot examples al `_CLASSIFIER_PROMPT_TEMPLATE` (time, math, notes, search, projects, none)
- [x] **1.10** Tests unitarios (11 tests nuevos en 4 archivos):
  - `test_guardrails.py`: `test_check_tool_coherence_uses_think_false`, `test_check_hallucination_uses_think_false`
  - `test_memory_flush.py`: `test_flush_to_memory_uses_think_false`
  - `test_summarizer.py`: `test_maybe_summarize_uses_think_false`
  - `test_consolidator.py`: `test_consolidate_uses_think_false`
  - `tests/test_compaction.py` (nuevo): 5 tests incluyendo `test_compact_llm_uses_think_false`, `test_compact_prompt_has_no_full_result_note`
  - `test_tool_router.py`: `test_classify_prompt_includes_few_shot_examples`
- [x] **1.11** `make check` (lint + typecheck + tests) — 518 passed, 0 errors

### Phase 2: Prompt Registry

- [x] **2.1** Crear `app/eval/prompt_registry.py` — catálogo centralizado:
  ```python
  """Centralized prompt catalog with default content for all named prompts."""

  PROMPT_DEFAULTS: dict[str, str] = {
      "system_prompt": "...",        # from config.py
      "classifier": "...",           # from router.py _CLASSIFIER_PROMPT_TEMPLATE
      "summarizer": "...",           # from summarizer.py SUMMARIZE_PROMPT
      "flush_to_memory": "...",      # from summarizer.py FLUSH_PROMPT
      "consolidator": "...",         # from consolidator.py CONSOLIDATE_PROMPT
      "compaction_system": "...",    # from compaction.py system msg
      "compaction_user": "...",      # from compaction.py user msg template
      "planner_create": "...",       # from planner.py
      "planner_replan": "...",       # from planner.py
      "planner_synthesize": "...",   # from planner.py
  }

  def get_default(prompt_name: str) -> str | None:
      return PROMPT_DEFAULTS.get(prompt_name)
  ```
- [x] **2.2** Extender `prompt_manager.py` — `get_active_prompt()` usa `prompt_registry.get_default()` como fallback en vez de requerir `default` param:
  ```python
  async def get_active_prompt(prompt_name: str, repository, default: str | None = None) -> str:
      # 1. Check cache
      # 2. Check DB
      # 3. Fall back to registry default
      # 4. Fall back to explicit default param
      # 5. Raise if nothing found
  ```
- [x] **2.3** `config.py`: Agregar `prompt_versioning_enabled: bool = True`
- [x] **2.4** `repository.py`: Método `seed_default_prompts(defaults: dict[str, str])`:
  ```python
  async def seed_default_prompts(self, defaults: dict[str, str]) -> int:
      """Insert v1 for any prompt_name not yet in prompt_versions. Returns count seeded."""
      seeded = 0
      for name, content in defaults.items():
          existing = await self.get_active_prompt_version(name)
          if existing is None:
              await self.save_prompt_version(name, version=1, content=content, created_by="system")
              await self.activate_prompt_version(name, version=1)
              seeded += 1
      return seeded
  ```
- [x] **2.5** `main.py` lifespan: Llamar `repository.seed_default_prompts(PROMPT_DEFAULTS)` en startup (después de `init_db`, antes de warmup). Gated por `settings.prompt_versioning_enabled`.
- [x] **2.6** Migrar `classify_intent()` en `router.py`:
  - Reemplazar `_CLASSIFIER_PROMPT_TEMPLATE` hardcodeado por `get_active_prompt("classifier", repository)`
  - Nota: `classify_intent` necesita `repository` como nuevo param (o recibirlo via caller)
  - El template sigue teniendo placeholders `{categories}`, `{recent_context}`, `{user_message}` → `.format()` después de obtenerlo del registry
- [x] **2.7** Migrar `maybe_summarize()` y `flush_to_memory()` en `summarizer.py`
- [x] **2.8** Migrar `consolidate_memories()` en `consolidator.py`
- [x] **2.9** `compaction.py`: registrado en catálogo para seeding/introspección; dynamic wiring diferido (executor no tiene repository access, Media priority)
- [x] **2.10** Migrar planner prompts en `planner.py` — `repository: object | None = None` en `create_plan`, `replan`, `synthesize`
- [x] **2.11** Tests (19 tests en `tests/test_prompt_registry.py`): seed, idempotencia, fallback chain, cache, classify_intent con versión de DB
- [x] **2.12** `make check` — 537 passed, lint clean, mypy clean

### Phase 3: Eval-Coupled Versioning

- [x] **3.1** `prompt_manager.py`: Nueva función `activate_with_eval()` — LLM-as-judge, retorna score sin activar
- [x] **3.2** `commands/builtins.py`: `/approve-prompt` muestra eval score (✅/⚠️ advisory, activa siempre)
  - Import movido a module level para que `patch()` funcione en tests
- [x] **3.3** `eval_tools.py`: `run_quick_eval` acepta `prompt_name`/`prompt_version` opcionales — inyecta como system msg
- [x] **3.4** `registry.py`: Agregar `get_tool(name)` a `SkillRegistry` (requerido por tests)
- [x] **3.5** Tests (11 tests en `tests/test_eval_coupled_versioning.py`):
  - `activate_with_eval`: error en versión inexistente, dataset vacío, nunca activa, score bajo/alto threshold
  - `run_quick_eval`: override de prompt, versión inexistente retorna error
  - `/approve-prompt`: muestra score, warning en score bajo, activa si eval falla, skip si no hay ollama
- [x] **3.6** `make check` — 548 passed, lint clean, mypy clean

### Phase 4: Prompt Catalog Command

- [x] **4.1** `commands/builtins.py`: Comando `/prompts [nombre] [versión]`:
  - Sin args → lista todos con versión activa + creador + fecha
  - `<name>` → contenido activo (truncado a 600 chars) + historial de versiones
  - `<name> <version>` → contenido de versión específica (truncado a 800 chars) + marker ✅ si activo
- [x] **4.2** `repository.py`: `list_all_active_prompts()` ya existía (creada en Fase 2); se reutiliza sin cambios
- [x] **4.3** Tests (11 tests en `tests/test_prompt_catalog_command.py`):
  - DB vacía, lista con múltiples prompts, hint en salida
  - `<name>`: contenido activo, historial, no encontrado, truncado
  - `<name> <version>`: contenido específico, marker activo, no encontrado, versión inválida
- [x] **4.4** `make check` — 559 passed, lint clean, mypy clean

### Phase 5: Documentación

- [x] **5.1** Crear `docs/features/32-prompt_engineering.md`
- [x] **5.2** Crear `docs/testing/32-prompt_engineering_testing.md`
- [x] **5.3** Actualizar `docs/features/README.md` y `docs/testing/README.md`
- [x] **5.4** Actualizar `CLAUDE.md` con patrones del prompt registry y eval-coupled versioning
- [x] **5.5** `AGENTS.md` — no hay skill ni módulo nuevo que agregar (todo se integró en módulos existentes)

---

## Decisiones Técnicas

### ¿Por qué un catálogo centralizado (`prompt_registry.py`) y no mantener defaults en cada módulo?

Tener los defaults en un solo lugar permite:
1. `seed_default_prompts()` los inserta todos en un loop sin importar de dónde vienen
2. `propose_prompt_change()` puede operar sobre cualquier prompt sin conocer su módulo fuente
3. `/prompts` puede listar todo sin importar módulos
4. Evita circular imports (si el registry importara de cada módulo)

Los módulos originales (`router.py`, `summarizer.py`, etc.) dejan de tener la constante hardcodeada
y en su lugar llaman `get_active_prompt(name, repository)`.

### ¿Por qué no auto-activar en `activate_with_eval()`?

El eval es advisory: muestra el score pero el humano decide. Razones:
1. El dataset puede no cubrir el caso de uso del nuevo prompt
2. Un score bajo podría ser aceptable si el cambio es intencional
3. El humano necesita ver qué evaluó y decidir

### ¿Por qué `think=False` en TODOS los prompts JSON/binarios?

qwen3.5:9b con `think=True` genera tokens `<think>...</think>` antes del output. Para prompts que
esperan JSON (`flush_to_memory`, `consolidator`) o respuestas binarias (`yes`/`no`), esto:
1. Agrega 500-2000ms de latencia innecesaria
2. Puede contaminar el JSON con texto antes del `{`
3. Consume tokens del context window sin beneficio

### ¿Por qué few-shot en el classifier y no fine-tuning?

1. qwen3.5:9b via Ollama no soporta LoRA hot-swap fácilmente
2. Few-shot es iterativo: se puede versionar el prompt con nuevos examples
3. 5 examples agregan ~100 tokens — negligible en un context de 32K
4. El pattern "examples in system prompt" es la recomendación de Qwen docs para clasificación

### ¿Por qué pre-fetch de prompt en caller para compaction?

`compact_tool_output()` es una función de formatting — no debería tener acoplamiento a la DB.
El caller (`executor.py`) ya tiene acceso a repository, así que pre-fetches el prompt y lo pasa
como param. Esto mantiene `compaction.py` como módulo puro de transformación.
