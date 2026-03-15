# PRD: Context Engineering v2 — Optimización del Context Window

## 1. Objetivo y Contexto

LocalForge usa qwen3.5:9b (32K context window) como LLM principal. El contexto se construye
inyectando múltiples bloques (system prompt, perfil, memorias, notas, daily logs,
capabilities, summary, historial de 20 mensajes) sin medir cuántos tokens consume ni
cuánto de eso es realmente relevante para el mensaje actual.

**Problema observado:** La investigación de mejores prácticas de context engineering
(Anthropic sep 2025, Factory.ai, Chroma "Context Rot") confirma que:
- "A focused 300-token context often outperforms an unfocused 113K-token context"
- Modelos 8B pierden ~44% de accuracy con herramientas excesivas o contexto disperso
- "Models do not use their context uniformly; performance grows increasingly unreliable
  as input length grows"

**Estado actual:** No sabemos cuántos tokens enviamos por request. No filtramos por
relevancia. Inyectamos capabilities incluso cuando no hay tools. El historial se envía
crudo (20 msgs) sin compresión. Los system messages son planos sin estructura.

**Objetivo:** Reducir el contexto al mínimo de alta señal por request, medir el uso,
y estructurar las inyecciones para maximizar la comprensión de qwen3.5:9b.

## 2. Alcance

### In Scope
- **Token budget tracking**: estimar tokens por request, loguear, alertar si se acerca al límite
- **System prompt consolidation**: consolidar 6-7 system messages en 1-2 con secciones XML
- **History windowing**: últimos N mensajes verbatim + summary de los anteriores (ventana deslizante)
- **Capabilities filtering**: solo inyectar capabilities relevantes a las categorías clasificadas
- **Memory relevance filtering**: threshold de similarity mínimo para memorias y notas
- **ConversationContext.build() adoption**: migrar `_run_normal_flow` a usar el dataclass existente
- **Agent scratchpad**: activar el campo `scratchpad` de `ConversationContext` para persistir estado entre rounds del agente

### Out of Scope
- Cambiar el modelo (qwen3.5:9b se queda)
- Prompt templates por intent category (futura iteración)
- Sub-agent architectures (ya implementado parcialmente con planner-orchestrator)
- Role isolation para contenido externo (seguridad, diferente prioridad)
- Cambios al tool router o al compaction de tool outputs (ya bien implementados)
- Cambios al agent planner-orchestrator (loop structure se queda)

## 3. Casos de Uso Críticos

1. **Mensaje simple "hola"**: El contexto no debería incluir capabilities, notas, ni daily
   logs. Solo: system prompt + perfil + historial reciente (5 msgs) + memories relevantes.
   Budget estimado: ~2K tokens.

2. **Mensaje con tools "qué tiempo hace en Buenos Aires"**: El contexto incluye capabilities
   **solo** de la categoría `weather`. Memorias filtradas por similarity > threshold.
   Budget estimado: ~3K tokens.

3. **Conversación larga (40+ mensajes)**: En lugar de 20 mensajes crudos, se inyectan los
   últimos 8 verbatim + un summary de los anteriores. El summary ya existe en DB; solo
   cambia cómo se arma la ventana.

4. **Sesión agéntica round 10/15**: El agente usa el scratchpad para persistir hallazgos
   clave. En cada round se inyecta el scratchpad (~200 tokens) en lugar de re-cargar todo
   el historial de tool results.

5. **Request con muchas memorias (30+)**: Solo las top-K con similarity > 0.5 se inyectan.
   Si ninguna pasa el threshold, se inyectan las top-3 como fallback.

## 4. Decisiones Arquitectónicas

### ¿Por qué consolidar system messages en lugar de dejarlos separados?

Ollama (con qwen3.5:9b) procesa múltiples `role=system` como bloques separados de instrucciones.
La atención se fragmenta. Anthropic recomienda explícitamente usar XML tags o Markdown headers
dentro de un solo system message para que el modelo pueda "navegar" secciones. Con modelos
pequeños esto es aún más crítico porque el attention budget es limitado.

### ¿Por qué chars/4 como estimador de tokens y no un tokenizer real?

qwen3.5:9b usa un tokenizer BPE propio. Integrar tiktoken o el tokenizer de Hugging Face
agrega dependencia pesada. chars/4 es un proxy aceptable (±20%) para logging y alertas.
Si en el futuro se necesita precisión, se puede swapear por el tokenizer real sin cambiar
la interfaz.

### ¿Por qué ventana deslizante y no summarization on-the-fly?

Ya existe `maybe_summarize` que crea summaries en background después de 40 mensajes.
Implementar summarization síncrona por request agrega latencia (~3s por llamada LLM).
La ventana deslizante (últimos N verbatim + summary existente) es zero-latency y usa
datos que ya están en DB.

### ¿Por qué usar ConversationContext.build() en vez del código manual?

`_run_normal_flow()` reimplementa manualmente lo que `ConversationContext.build()` ya
hace: fetches paralelos de memorias, historial, summary, sticky_categories, user_facts.
Centralizar en `ConversationContext` reduce duplicación, facilita testing, y asegura que
futuros campos se propaguen sin tocar el router.

### ¿Por qué el scratchpad es un string y no structured data?

El scratchpad es un espacio libre donde el agente escribe notas entre rounds. Hacerlo
structured (JSON/dataclass) requiere que el LLM genere datos válidos y parsear, lo cual
falla con qwen3.5:9b ~20% del tiempo. Un string libre es más robusto.

## 5. Restricciones

- **Zero-latency adicional**: ningún cambio debe agregar llamadas LLM o DB al critical path
- **Backwards compatible**: si `ConversationContext.build()` falla, fallback al flujo actual
- **Fail-open**: si el token estimator calcula >32K, loguear WARNING pero no bloquear
- **Tests**: cada cambio debe tener tests unitarios; `make check` debe pasar
- **No breaking changes**: la firma de `_build_context()` puede cambiar internamente pero
  el resultado (list[ChatMessage]) sigue igual
- **Agent loop**: cambios al scratchpad no deben requerir cambios en `planner.py` o `workers.py`

## 6. Métricas de Éxito

- **Token usage logged**: cada request tiene un log entry con `estimated_tokens_input`
- **Reducción medible**: mensajes simples pasan de ~X tokens (medir antes) a ~Y tokens
- **Capabilities no inyectadas**: para `classify_intent == ["none"]`, log confirma 0 capabilities
- **History windowed**: mensajes con >8 historial usan ventana deslizante + summary
- **Memorias filtradas**: log muestra `memories_injected` < `memories_total` cuando hay threshold
- **Agent scratchpad**: sesiones agénticas >5 rounds tienen scratchpad non-empty
- **make check**: lint + typecheck + tests pasan

## 7. Referencias

- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Factory.ai: The Context Window Problem](https://factory.ai/news/context-window-problem)
- [Prompting Guide: Context Engineering Guide](https://www.promptingguide.ai/guides/context-engineering-guide)
- [FlowHunt: Context Engineering Definitive Guide 2025](https://www.flowhunt.io/blog/context-engineering/)
- [CodeConductor: Context Engineering Complete Guide 2026](https://codeconductor.ai/blog/context-engineering)
