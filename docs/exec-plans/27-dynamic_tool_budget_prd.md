# PRD: Dynamic Tool Budget & `request_more_tools`

## 1. Objetivo y Contexto

El sistema de selección de tools de LocalForge tiene un bug crítico de producción: cuando el
pre-clasificador de intención elige múltiples categorías, `select_tools()` llena el budget
de 8 tools con la primera categoría sin distribuir el cupo entre las demás. Resultado
observado en logs:

```
categories=['projects', 'github'], selected 8 tools: [todos project tools, 0 github tools]
```

El LLM recibe 0 tools de GitHub → no puede actuar → presenta un plan en lugar de ejecutarlo.

Además, aunque el clasificador falle completamente (elija categorías incorrectas), el LLM
no tiene mecanismo de escape: está atado al tool set inicial sin forma de pedir lo que necesita.

## 2. Alcance

**In Scope:**
- Fix de distribución proporcional de budget en `select_tools()` (bug inmediato)
- Meta-tool `request_more_tools` para expansión dinámica dentro del loop (arquitectura)
- Tests unitarios para ambos cambios
- Mención en `_build_capabilities_section()` para que el LLM conozca el meta-tool

**Out of Scope:**
- Embedding-based tool retrieval (documentado como Opción C futura — ver sección al final)
- Cambios al pre-clasificador `classify_intent()`
- Cambios al número máximo de iteraciones del loop (5)
- Cambios a la política de seguridad (`PolicyEngine`) — `request_more_tools` no pasa por ella

## 3. Casos de Uso Críticos

1. **Multi-categoría con budget overflow:** Usuario pide "crear landing page en GitHub basándome
   en otro repo" → clasificador devuelve `['projects', 'github']` → ambas categorías deben recibir
   tools → LLM puede ejecutar sin pedir ayuda.

2. **Clasificador incorrecto o incompleto:** Clasificador devuelve `['notes']` para un mensaje que
   también requiere GitHub → LLM reconoce que le faltan tools → llama `request_more_tools(['github'])`
   → siguiente iteración tiene las tools correctas → ejecuta la tarea.

3. **Clasificador correcto, una categoría:** `['search']` → per_cat = max(2, 8//1) = 8 → sin cambio
   en comportamiento, retrocompatibilidad garantizada.

## 4. Decisiones Arquitectónicas

### ¿Por qué `request_more_tools` y no aumentar el cap?

Aumentar el cap (de 8 a 16) resolvería el bug inmediato pero no el problema raíz: si el clasificador
elige categorías incorrectas, más tools no ayudan. Además, más tools en contexto = mayor probabilidad
de que qwen3.5:9b elija la tool equivocada (accuracy degrada después de ~30 tools según Anthropic).

### ¿Por qué no Tool-RAG embedding-based ahora?

LocalForge tiene la infraestructura (nomic-embed-text + sqlite-vec), pero con ~50 tools actuales el
beneficio marginal no justifica la complejidad. `request_more_tools` es más simple, funciona con
qwen3.5:9b, y no requiere indexación. Tool-RAG queda documentado para cuando tools > 50.

### Referencia de industria

Este patrón es el que Anthropic implementó como `tool_search_tool` en la API (Sonnet 4.0+, Feb 2026)
y el que describe el paper ITR (arxiv:2602.17046): 95% reducción de contexto, 32% mejora de accuracy.
LocalForge implementa una versión simplificada (por categorías, no embedding search) adecuada para
qwen3.5:9b local.

## 5. Restricciones

- `request_more_tools` NO debe pasar por `PolicyEngine` ni `AuditTrail` — es meta-infraestructura
- El meta-tool NO cuenta contra el budget de categorías (siempre presente, prepended)
- El handler del meta-tool vive en `executor.py` (inline), no en `SkillRegistry` — modifica
  estado del loop, no puede ser una tool normal
- Compatible con el loop existente de 5 iteraciones — `request_more_tools` usa 1 iteración
- `select_tools()` debe mantener retrocompatibilidad: 1 categoría → mismo comportamiento de antes

## 6. Opción C — Futuro: Embedding-based Tool Retrieval

> Documentado aquí para registro de decisión. No implementar hasta que tools > 50.

**Concepto:** Embeder todas las tool descriptions al registrarlas. En cada request, embeder el
user message → cosine search en sqlite-vec → top-K tools directamente, sin categorías.

**Por qué LocalForge está bien posicionado:**
- `nomic-embed-text` ya corre en Ollama
- `sqlite-vec` ya disponible en `app/database/db.py`
- `embed_note()` y `embed_memory()` en `app/embeddings/indexer.py` son el patrón a replicar

**Trabajo estimado:**
1. Tabla `vec_tools` en `SCHEMA` de `db.py`
2. `embed_tool_descriptions()` en `indexer.py` — llamado en `register_tool()` del registry
3. `repository.search_tools_by_embedding(query_vec, top_k)` en `repository.py`
4. Reemplazar `select_tools()` con búsqueda embedding en el executor
5. Mantener `request_more_tools` como escape hatch (embedding search tiene falsos negativos)

**Referencia:** arxiv:2602.17046 (ITR) — 95% reducción de contexto, $0.86 vs $2.90 por episodio.
