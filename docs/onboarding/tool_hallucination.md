# Tool Hallucination — Guía de Onboarding

> **Para quién es esto:** desarrollador junior que se une al proyecto y necesita entender
> por qué un LLM a veces "inventa" información o usa herramientas mal, y qué defensas
> tiene el sistema para prevenirlo.
>
> **Prerequisito:** haber leído el `CLAUDE.md` principal y el doc de
> [Skills y Herramientas](../features/06-skills_herramientas.md). Entender que el LLM
> interactúa con el mundo exterior a través de **tool calls** (funciones que el modelo
> pide ejecutar).

---

## 1. Qué es tool hallucination y por qué importa

En un sistema como LocalForge, el LLM no solo genera texto — también decide cuándo y
cómo usar herramientas (buscar en GitHub, leer archivos, guardar notas, etc.). "Tool
hallucination" es cuando el modelo falla en esta decisión de alguna de estas formas:

| Tipo | Qué pasa | Ejemplo real del proyecto |
|------|----------|--------------------------|
| **Respuesta prematura** | El modelo responde con datos inventados en vez de usar tools para obtenerlos | El usuario pidió analizar un repo en GitHub. El LLM leyó solo el listado de carpetas y fabricó "React 18+, PostgreSQL, JWT, 72% test coverage" — nada de eso existía en el repo |
| **Schema hallucination** | El modelo llama una tool con parámetros que no existen en su definición | `create_feature_docs(manual_content=..., category=...)` — la tool espera `feature_id`, `feature_title`, `walkthrough_content`, `testing_content`. El LLM inventó nombres 4 veces seguidas |
| **Tool fantasma** | El modelo intenta llamar una tool que no existe | `fetch_file` — no existe ninguna tool con ese nombre en el sistema |

Estos no son bugs en el código — son fallas probabilísticas del modelo. El código funciona
correctamente; es el modelo el que decide mal. Pero podemos construir defensas en el código
para reducir la frecuencia y el impacto.

---

## 2. Por qué pasa: la mecánica detrás del error

### 2.1 El LLM no "sabe" — predice

Un LLM genera texto token por token. Cuando tiene tools disponibles, puede generar un
JSON de tool call O texto directo. La decisión es probabilística. Si el modelo "cree" que
ya tiene suficiente contexto para responder (porque vio un directory listing que parece
un proyecto típico), va a generar texto directamente sin hacer más tool calls.

```
Usuario: "Analiza el repo local-forge de @sebadp en GitHub"

Lo que pasó:
  1. Tool: search_repositories("sebadp") → lista de repos (OK)
  2. Tool: get_file_contents(repo, path="") → directory listing (OK)
  3. ❌ El LLM decidió responder con un "análisis" inventado
     en vez de leer README.md, requirements.txt, etc.

Lo que debió pasar:
  1. Tool: search_repositories("sebadp") → lista de repos
  2. Tool: get_file_contents(repo, path="") → directory listing
  3. Tool: get_file_contents(repo, path="README.md") → contenido real
  4. Tool: get_file_contents(repo, path="requirements.txt") → dependencias reales
  5. Responder con datos verificados
```

### 2.2 Modelos más chicos hallucinan más

Usamos **qwen3:8b** (8 mil millones de parámetros) que corre localmente. Es un modelo
capaz, pero tiene menos "paciencia" para cadenas largas de tool calls comparado con
modelos más grandes (GPT-4, Claude, Qwen3-32B). A veces toma atajos y responde con
lo que infiere del contexto parcial.

Un paper de octubre 2025 — [The Reasoning Trap](https://arxiv.org/html/2510.22977v1)
— demostró que **mejorar el reasoning de un LLM puede empeorar su tool calling**.
El modelo razona tan bien que se convence de que ya sabe la respuesta sin necesitar
verificarla con tools.

### 2.3 Schema hallucination ocurre por entrenamiento genérico

El modelo fue entrenado con miles de APIs diferentes. Cuando ve una tool llamada
`create_feature_docs`, "adivina" que probablemente tiene parámetros como `category`
o `content` porque eso es común en APIs. No lee el schema con la atención que
debería.

---

## 3. Las defensas del sistema (dónde vive cada una)

### 3.1 Grounding Rule en el system prompt

**Archivo:** `app/config.py` — campo `system_prompt`

```python
"GROUNDING RULE: Never fabricate specific facts (tech stacks, percentages, metrics, "
"file contents) without reading actual data via tools first. If you only have partial "
"information (e.g. a directory listing), say what you see and use tools to read key "
"files (README, config files, package.json, requirements.txt) before making claims. "
"If a tool call fails, report the error honestly — do not invent the answer."
```

Esta instrucción le dice al modelo explícitamente que no invente datos. No es infalible
(el modelo puede ignorarla), pero reduce significativamente la tasa de hallucination.

**Cuándo importa:** Cada vez que modifiques el system prompt, asegurate de que esta
regla siga presente. Si la borrás o la diluís, vas a ver más respuestas inventadas.

### 3.2 Error messages enriquecidos en tool execution

**Archivo:** `app/skills/registry.py` — método `execute_tool()`

Cuando el LLM envía parámetros incorrectos, el sistema ahora responde con el schema
correcto:

```
ANTES (inútil para el LLM):
  "Tool error: create_feature_docs() got an unexpected keyword argument 'category'"

AHORA (el LLM puede auto-corregirse):
  "Tool error: create_feature_docs() got an unexpected keyword argument 'category'.
   Expected parameters: ['feature_id', 'feature_title', 'walkthrough_content', 'testing_content'].
   Required: ['feature_id', 'feature_title', 'walkthrough_content', 'testing_content'].
   You provided: ['category', 'manual_content']."
```

Esto funciona porque el tool loop tiene hasta 5 iteraciones. Si la primera falla, el LLM
recibe el error enriquecido y en la siguiente iteración tiene toda la información para
hacer la llamada correcta.

```
┌──────────────┐     tool call con args inventados
│   LLM        │ ──────────────────────────────────► execute_tool()
│              │                                         │
│              │ ◄─── "Error: expected [a, b, c],  ◄─────┘
│              │       you sent [x, y]"             TypeError handler
│              │
│  (iteración  │     tool call con args correctos
│   siguiente) │ ──────────────────────────────────► execute_tool() ✅
└──────────────┘
```

**Cómo funciona en el código:**

```python
# app/skills/registry.py — execute_tool()
except TypeError as e:
    # Schema mismatch — inyectar los parámetros reales en el error
    error_msg = str(e)
    if "unexpected keyword argument" in error_msg or "required" in error_msg:
        expected = list(tool.parameters.get("properties", {}).keys())
        required = tool.parameters.get("required", [])
        error_msg = (
            f"Tool error: {e}. "
            f"Expected parameters: {expected}. Required: {required}. "
            f"You provided: {list(tool_call.arguments.keys())}."
        )
```

### 3.3 Intent classification con contexto (sticky categories)

**Archivos:** `app/webhook/router.py` (Phase C) + `app/skills/router.py` (classify_intent)

**El problema:** El usuario tiene una conversación sobre GitHub:

```
Mensaje 1: "Revisa mi repo en GitHub"     → clasificado como ["github"] ✅
Mensaje 2: "Busca en el contenido y evalúa" → clasificado como ["search", "evaluation"] ❌
```

El segundo mensaje debería haber mantenido el contexto "github", pero el clasificador
rápido (Stage 1) corre SIN historial de conversación — es un `asyncio.create_task()`
que se lanza en paralelo con la carga de contexto para no agregar latencia.

**Cómo funciona la clasificación en dos stages:**

```
                    ┌─────────────────────────┐
                    │ Mensaje del usuario      │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              │                                     │
    Stage 1 (rápido, sin contexto)          Phase A/B (carga de contexto)
    classify_intent(texto)                  embed, memories, history...
              │                                     │
              ▼                                     │
    ¿Resultado = "none"?                            │
    O ¿sticky_categories no se solapan?             │
              │                                     │
         ┌────┴────┐                                │
         │ SÍ      │ NO ──► usar resultado directo  │
         ▼         │                                │
    Stage 2 (con contexto)  ◄───────────────────────┘
    classify_intent(texto, history, sticky_categories)
              │
              ▼
    Resultado final con contexto conversacional
```

**El fix aplicado:** Antes, Stage 2 solo corría si Stage 1 retornaba `"none"`. Ahora
también corre cuando hay `sticky_categories` que no se solapan con el resultado de
Stage 1. Esto captura el caso "el usuario sigue hablando de GitHub pero usó palabras
como 'busca' que matchean con otra categoría".

```python
# app/webhook/router.py — Phase C
elif sticky_categories and base_result != ["none"]:
    # Stage 1 clasificó, pero las sticky categories no se solapan —
    # re-clasificar con contexto para no perder continuidad
    if not set(sticky_categories) & set(base_result):
        needs_context_upgrade = True
```

### 3.4 Guardrails post-generación

**Archivo:** `app/guardrails/pipeline.py`

Incluso si el LLM hallucina, los guardrails pueden atrapar ciertos problemas ANTES
de enviar la respuesta al usuario:

- `language_match` — detecta si la respuesta está en un idioma diferente al del usuario
- `excessive_length` — respuestas demasiado largas (>8000 chars)
- `no_raw_tool_json` — detecta JSON crudo de tool calls filtrado en la respuesta
- `not_empty` — respuesta vacía

Cuando un guardrail falla, el sistema hace **remediation single-shot**: le pide al LLM
que corrija su respuesta (e.g., traducir al idioma correcto). No es recursivo — si la
corrección también falla el guardrail, se envía igual (fail-open).

### 3.5 Resolución de trace IDs truncados

**Archivo:** `app/database/repository.py` — método `_resolve_trace_id()`

**El problema:** `review_interactions` muestra IDs de 12 caracteres (`fa9a6817541c`)
pero la DB almacena IDs de 32 caracteres (`fa9a6817541c4a8b9e2d...`). Cuando el LLM
pasaba el ID corto a `get_tool_output_full`, la query `WHERE trace_id = ?` no encontraba
nada.

**El fix:** Un helper que expande prefijos truncados:

```python
async def _resolve_trace_id(self, trace_id: str) -> str | None:
    if len(trace_id) >= 32:
        return trace_id  # Ya es completo
    cursor = await self._conn.execute(
        "SELECT id FROM traces WHERE id LIKE ? LIMIT 1",
        (trace_id + "%",),
    )
    row = await cursor.fetchone()
    return row[0] if row else None
```

Aplicado en `get_trace_tool_calls`, `get_trace_scores`, y `get_trace_with_spans`.

---

## 4. Anatomía de un caso real: la conversación de GitHub

Veamos paso a paso lo que ocurrió para entender cómo interactúan todas las piezas.

### Turno 1: "Revisa mi repositorio en github soy sebadp busca el repo local-forge"

```
14:58:53  Incoming message
14:59:36  classify_intent → ["github"] ✅
14:59:36  Tool router: 8 tools (list_issues, search_repositories, get_file_contents, ...)
14:59:49  Iter 1: search_repositories("sebadp local-forge") → 0 resultados
15:00:01  Iter 2: search_repositories("sebadp") → 45 resultados (49,547 chars)
          ⚠️ Compaction: 49,547 → 19,702 chars (JSON extraction)
15:00:54  Iter 3: LLM responde directamente (EN INGLÉS, demasiado largo)
          ⚠️ Guardrails fallan: excessive_length + language_match
15:01:00  Remediation: LLM re-genera en español, más corto
15:01:01  Outgoing: respuesta remediada ✅
```

**Problemas:**
- El LLM vio 45 repos y fabricó un "perfil de desarrollador" sin que nadie se lo pidiera
- Respondió en inglés (guardrail lo corrigió)
- Nunca encontró el repo específico "local-forge" porque la primera búsqueda fue
  demasiado específica y la segunda demasiado amplia

### Turno 2: "Busca en el contenido y evalúa como un arquitecto de software"

```
15:02:00  Incoming message
15:03:17  classify_intent → ["search", "evaluation"] ❌ (debió ser ["github"])
          sticky_categories era ["github"] pero Stage 2 nunca corrió
15:03:17  Tool router: web_search, get_eval_summary, list_recent_failures, ... (NO github tools)
15:03:24  Iter 1: web_search("GitHub sebadp local-forge review") → resultados genéricos
15:03:38  Iter 2: request_more_tools(["github"]) → 8 github tools added ✅ (salvada parcial)
15:03:43  Iter 3: get_file_contents(repo, path=undefined) → ERROR (path requerido)
15:03:48  Iter 4: get_file_contents(repo, path="") → directory listing
15:04:12  Iter 5: LLM responde con "análisis de arquitectura" FABRICADO
          ⚠️ Guardrail: language_match (mezcló inglés con español)
```

**Problemas:**
1. Clasificación incorrecta → tools incorrectas → desperdició 2 iteraciones
2. `request_more_tools` salvó parcialmente pero consumió 1 iteración de las 5
3. Solo quedaron 2 iteraciones útiles con github tools (de 5 posibles)
4. El LLM solo vio el directory listing y fabricó todo el análisis
5. Con el fix de sticky categories, Stage 2 hubiera corrido y clasificado como
   `["github"]` desde el inicio

### Turno 3: "Sí, crea la documentación de arquitectura"

```
15:29:45  Incoming message
15:30:06  classify_intent → ["documentation"]
15:30:06  Tool router: create_feature_docs, update_architecture_rules, update_agent_docs
15:30:45  Iter 1: create_feature_docs(manual_content=...) → TypeError ❌
15:31:23  Iter 2: create_feature_docs(category=...) → TypeError ❌
15:31:59  Iter 3: create_feature_docs(category=...) → TypeError ❌ (mismo error!)
15:32:36  Iter 4: create_feature_docs(testing_content_category=...) → TypeError ❌
15:32:42  Iter 5: fetch_file(...) → "Unknown tool" ❌
          ⚠️ Max iterations reached
15:33:00  Outgoing: "No puedo generar automáticamente... aquí tienes el contenido"
```

**Problemas:**
1. 4 intentos con argumentos inventados — el error message no incluía los params correctos
2. En el intento 3, el LLM repitió el MISMO error del intento 2 (sin info para corregirse)
3. Con el fix de error messages enriquecidos, el intento 2 hubiera corregido los argumentos
4. Además, `create_feature_docs` es para documentar features del PROPIO proyecto, no de
   repos externos — hay un mismatch de intención que el clasificador no puede resolver

---

## 5. Qué hacer cuando ves estos problemas

### Si ves hallucination de datos en los logs

1. **Verificá el system prompt** — ¿sigue presente la GROUNDING RULE? (`app/config.py`)
2. **Mirá cuántas iteraciones usó** — si el LLM respondió en iteración 1-2 sin hacer
   tool calls suficientes, el problema es respuesta prematura
3. **Considerá si el modelo es adecuado** — para tareas que requieren cadenas largas de
   tool calls (5+), qwen3:8b puede no ser suficiente. Modelos más grandes como
   qwen3:32b o qwen3.5 tienen mucho mejor adherencia a tools

### Si ves schema hallucination (argumentos incorrectos)

1. **Verificá el error message** — ¿incluye los parámetros esperados? Si no, revisá
   el `TypeError` handler en `registry.py`
2. **Revisá el schema de la tool** — ¿los nombres de los parámetros son intuitivos?
   `walkthrough_content` es menos obvio que `content`. Nombres descriptivos reducen
   hallucination
3. **Revisá la `description` de cada parameter** — el LLM lee las descriptions del schema.
   Si son vagas o ausentes, va a adivinar

### Si ves clasificación incorrecta

1. **Mirá las sticky categories** — `"Tool router: categories=[X]"` en los logs
2. **Verificá si Stage 2 corrió** — buscá `"falling back to sticky categories"` o
   `"Classifier returned 'none'"` en los logs
3. **Si sticky + base no se solapan**, el nuevo fix debería activar Stage 2
   automáticamente

---

## 6. Resumen: las capas de defensa

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MENSAJE DEL USUARIO                         │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAPA 1: Intent Classification (con sticky categories)              │
│  Defensa: re-clasificar con contexto si sticky no se solapa         │
│  Archivo: app/webhook/router.py (Phase C)                           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAPA 2: System Prompt (grounding rule)                             │
│  Defensa: instrucción explícita de no fabricar datos sin tools      │
│  Archivo: app/config.py → system_prompt                             │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAPA 3: Tool Execution (error messages enriquecidos)               │
│  Defensa: TypeError → incluir schema correcto para auto-corrección  │
│  Archivo: app/skills/registry.py → execute_tool()                   │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAPA 4: Guardrails post-generación                                 │
│  Defensa: language_match, excessive_length, no_raw_tool_json        │
│  Archivo: app/guardrails/pipeline.py                                │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  CAPA 5: Observabilidad (debug tools con prefix match)              │
│  Defensa: poder investigar qué pasó post-mortem                     │
│  Archivo: app/database/repository.py → _resolve_trace_id()          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Referencias

- [The Reasoning Trap: How Enhancing LLM Reasoning Amplifies Tool Hallucination](https://arxiv.org/html/2510.22977v1) — Paper clave sobre por qué modelos con mejor reasoning hallucinan más con tools
- [LLM-based Agents Suffer from Hallucinations: A Survey](https://arxiv.org/html/2509.18970v1) — Taxonomía completa de hallucination en agentes
- [Minimize LLM Hallucinations with Pydantic Validators](https://pydantic.dev/articles/llm-validation) — Validación de output + retry como patrón de mitigación
- [Docker: Local LLM Tool Calling Evaluation](https://www.docker.com/blog/local-llm-tool-calling-a-practical-evaluation/) — Benchmark de tool calling en modelos locales (Qwen3 vs Llama vs Mistral)
- [Stop AI Agent Hallucinations: 4 Essential Techniques](https://dev.to/aws/stop-ai-agent-hallucinations-4-essential-techniques-2i94) — Patrones prácticos: retrieval-first, verify step, strict grounding

---

## 8. Archivos clave para este tema

| Archivo | Qué hace | Cuándo tocarlo |
|---------|----------|----------------|
| `app/config.py` | System prompt con grounding rule | Si querés ajustar las instrucciones anti-hallucination |
| `app/skills/registry.py` | Ejecución de tools + error messages enriquecidos | Si agregás nuevas tools y querés que el LLM se auto-corrija |
| `app/skills/router.py` | Clasificación de intent + sticky categories | Si agregás nuevas categorías de tools |
| `app/webhook/router.py` | Pipeline completo: classify → tools → guardrails | Si necesitás entender el flujo end-to-end |
| `app/guardrails/pipeline.py` | Validación post-generación | Si querés agregar nuevos checks (e.g., detectar datos inventados) |
| `app/database/repository.py` | `_resolve_trace_id()` para debug tools | Si ves que debug tools no encuentran datos |
