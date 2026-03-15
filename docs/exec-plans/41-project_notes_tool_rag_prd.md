# PRD: Project Notes & Tool RAG — Plan 41

## Objetivo y Contexto

El sistema de proyectos no puede persistir contenido generado (capítulos, textos, documentos) ni recuperarlo después. En una sesión real, el LLM generó un prólogo y 2 capítulos para un libro pero:

1. **`add_project_note` nunca se selecciona** — posición 9 de 10 en la categoría `projects`, el budget de 8 tools la corta siempre
2. **No existe `get_project_note`** — incluso si se guardara, no hay forma de leer el contenido completo (search trunca a 120 chars)
3. **No existe `list_project_notes`** como tool — el usuario no puede ver qué notas tiene un proyecto
4. **Project notes son invisibles al LLM** — no se inyectan en contexto, no participan en búsqueda semántica del context builder
5. **No hay backfill de embeddings** para project notes al startup

El problema de selección de tools no se resuelve reordenando la lista (eso rompe otras capacidades). Se necesita un mecanismo donde el agente descubra tools dinámicamente según lo que necesita — esto es el patrón "Tool RAG" que Anthropic, OpenAI y AWS han productionizado en 2025-2026.

## Alcance (In Scope & Out of Scope)

### In Scope

**Fase A — Project Notes Completos:**
- Tool `get_project_note(note_id)` — contenido completo sin truncar
- Tool `list_project_notes(project_name)` — lista con previews de 300 chars
- Aumentar preview en `search_project_notes` de 120 → 500 chars
- Backfill de project note embeddings al startup
- Agregar `title` opcional al modelo `ProjectNote` (para documentos con nombre)

**Fase B — Tool RAG (Semantic Tool Discovery):**
- Tabla `vec_tools` con embeddings de tool descriptions
- `embed_tool_descriptions()` — embeddea todas las tool descriptions al startup
- Mejorar `request_more_tools` para aceptar `query` (lenguaje natural) además de `categories`
- Semantic search sobre tool descriptions cuando se recibe un query
- `select_tools()` augmentado: categoría primero, luego fill con semantic relevance para slots restantes

**Fase C — Project Notes en Contexto:**
- Inyectar project notes relevantes en `<project_notes>` cuando hay proyectos activos
- Buscar semánticamente en project notes usando el `query_embedding` existente
- Sección en `ContextBuilder` entre `<active_projects>` y `<relevant_notes>`

### Out of Scope
- Unificación de notes y project_notes (evaluado: costo > beneficio)
- Versionado de documentos (futuro)
- Graph-based tool routing (requiere datos históricos)
- Instruction-Tool Retrieval / ITR (overkill para MVP)
- Cambios al modelo de datos de `notes` (globales)

## Casos de Uso Críticos

### 1. Escritura colaborativa con persistencia
```
Usuario: "Comienza por escribir el prólogo del libro"
→ LLM genera contenido + guarda con add_project_note(title="Prólogo")
→ Tool disponible gracias a Tool RAG (semantic match con "guardar nota proyecto")

Usuario: "Muéstrame lo que tenemos escrito"
→ LLM llama list_project_notes → muestra títulos y previews
→ LLM llama get_project_note(id) para contenido completo si se pide

Usuario: "Continúa con el capítulo 1"
→ LLM lee prólogo via get_project_note → genera cap 1 con continuidad → guarda
```

### 2. Tool discovery dinámica
```
Usuario: "Guarda el progreso en notas del proyecto"
→ classify_intent retorna ["projects"]
→ select_tools selecciona 8 tools base de projects
→ add_project_note NO entra en budget
→ LLM llama request_more_tools(query="guardar nota en proyecto")
→ Semantic search encuentra add_project_note → se agrega al tool set
→ LLM guarda la nota exitosamente
```

### 3. Context awareness de project notes
```
Usuario: "¿Qué capítulos tenemos listos?"
→ ConversationContext.build() detecta proyectos activos
→ Busca project notes semánticamente con query_embedding
→ Inyecta resúmenes de notas relevantes en <project_notes>
→ LLM responde con conocimiento del contenido existente
```

## Restricciones Arquitectónicas / Requerimientos Técnicos

- **Embeddings**: usar `nomic-embed-text` existente (768 dims) + `sqlite-vec` existente
- **Best-effort**: errores de embedding nunca propagados (patrón existente)
- **Tool budget**: `max_tools_per_call=8` no cambia — Tool RAG llena slots inteligentemente
- **`request_more_tools`**: mantener backward-compatible (categories sigue funcionando, query es nuevo)
- **Backfill**: background task via `asyncio.create_task()` (no bloquea startup)
- **Token budget**: project notes en contexto deben respetar el budget de 32K tokens
- **qwen3.5:9b**: Tool RAG debe ser transparente al LLM — el modelo no necesita entender embeddings
- **Truncado**: eliminar truncados agresivos donde sea posible; cuando sea necesario usar 500+ chars, no 120

## Investigación de Referencia (Tool RAG)

| Fuente | Patrón | Relevancia |
|--------|--------|------------|
| Anthropic Tool Search (beta 2025-09) | Deferred loading + tool search meta-tool | Nuestro `request_more_tools` es equivalente |
| OpenAI Tool Search (Responses API) | Namespaces + deferred functions | Validación del patrón |
| Redis Tool RAG | Embed tool metadata → vector search → top-K | Implementación directa con nuestra infra |
| Red Hat Tool RAG | Dense + hybrid retrieval | Semantic + category = hybrid |
| AWS Strands SDK | Built-in semantic tool retrieval (6000+ tools) | Validación de escalabilidad |
| arXiv 2602.17046 (ITR) | Dynamic system instructions + tool exposure per step | Futuro: agent loop optimization |
