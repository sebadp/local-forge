# PRP: Session Memory — LLM-Powered Fact Extraction Continua (Plan 55)

## Archivos a Modificar

- `app/memory/session_extractor.py`: **Nuevo** — Prompt + extracción + persistencia
- `app/webhook/router.py`: Trigger de extracción como background task post-response
- `app/config.py`: Setting `SESSION_EXTRACT_INTERVAL` (default 10 mensajes)
- `app/context/fact_extractor.py`: Agregar facts del session extractor al contexto (opcional)
- `tests/test_session_extractor.py`: **Nuevo**

## Fases de Implementación

### Phase 1: Session Extractor Core

- [x] Crear `app/memory/session_extractor.py`:
  ```python
  SESSION_EXTRACT_PROMPT = """
  Analyze these recent messages and extract NEW facts about the user.
  
  Messages:
  {messages}
  
  Existing known facts:
  {existing_facts}
  
  Rules:
  - Only extract facts NOT already in existing_facts
  - Convert relative dates to absolute (today is {today})
  - Categories: preference, personal, technical, temporal, correction
  - If a fact contradicts an existing one, mark type as "correction"
  
  Return JSON:
  {{"facts": [
    {{"content": "...", "category": "...", "replaces_id": null}},
    {{"content": "...", "category": "temporal", "expires": "2026-04-15"}}
  ]}}
  If no new facts: {{"facts": []}}
  """
  ```
- [x] Implementar `extract_session_facts()`:
  - Args: `messages: list[ChatMessage]`, `existing_facts: list[str]`, `repository: Repository`, `ollama_client: OllamaClient`
  - Formatear mensajes recientes (solo user + assistant, skip system/tool)
  - Single LLM call con `think=False`
  - Parsear JSON response
  - Para cada fact: `repository.save_memory(content, category)` 
  - Para corrections: `repository.update_memory(replaces_id, new_content)`
  - Append al daily log via `daily_log.append_entry()`
  - Return count de facts extraídos
- [x] `SessionExtractTracker` — simple dict in-memory:
  ```python
  _message_counters: dict[str, int] = {}  # phone -> messages since last extract
  
  def should_extract(phone: str, interval: int = 10) -> bool:
      count = _message_counters.get(phone, 0) + 1
      _message_counters[phone] = count
      if count >= interval:
          _message_counters[phone] = 0
          return True
      return False
  ```
- [x] Tests con mocked Ollama: verify JSON parsing, fact creation, correction handling

### Phase 2: Webhook Integration

- [x] En `app/webhook/router.py`, en `_handle_message()`, después de enviar la respuesta:
  ```python
  # Post-response background extraction
  if session_extract_tracker.should_extract(phone_number):
      background_tasks.add_task(
          _run_session_extraction, 
          phone_number, repository, ollama_client, memory_file
      )
  ```
- [x] Implementar `_run_session_extraction()`:
  - Cargar últimos 20 mensajes del usuario (`repository.get_recent_messages(phone, limit=20)`)
  - Cargar memorias existentes como `existing_facts`
  - Llamar `extract_session_facts()`
  - Wrappear en try/except con logging best-effort
  - Tracing span: `async with TraceContext("session_extraction"):`
- [x] Agregar setting `session_extract_interval: int = 10` a `app/config.py`

### Phase 3: Context Integration (Opcional)

- [x] En `app/context/conversation_context.py`, dentro de `build()`:
  - Cargar facts con category="temporal" que no hayan expirado
  - Inyectarlos en el context como `<temporal_context>` tag
  - Esto le da al LLM awareness de eventos futuros del usuario
- [x] Tests de integración: verify temporal facts appear in context

### Phase 4: Documentación & QA

- [x] `make test` pasa
- [x] `make lint` pasa
- [x] Crear `docs/features/55-session_memory.md`
- [x] Actualizar `AGENTS.md` con el nuevo módulo
- [x] Actualizar `CLAUDE.md` con el patrón de background extraction
