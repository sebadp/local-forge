# PRD: Token Accuracy

> **Origen:** Gap 2.3 del [Plan de Arquitectura](42-architecture_action_plan.md) (Palantir AIP Gap Analysis)
> **Independiente** — no depende de otros planes

## Objetivo y Contexto

El sistema actual estima tokens con `chars/4`, un proxy que tiene ±20% de margen de error. Para qwen3 con su tokenizer BPE específico, esto puede sobre o sub-estimar significativamente, causando:

- **Context overflow**: si sub-estimamos, enviamos más tokens de los que el modelo soporta → truncamiento silencioso o error
- **Context underutilization**: si sobre-estimamos, dejamos espacio sin usar → respuestas menos informadas
- **Métricas imprecisas**: el token budget tracking (`log_context_budget()`) reporta warnings/errors que no reflejan la realidad
- **Budget decisions incorrectas**: `token_estimate` en `ConversationContext` puede disparar recortes innecesarios

Ollama ya devuelve `prompt_eval_count` (tokens de input reales) en cada respuesta. Este dato permite calibrar el proxy sin dependencias externas.

## Alcance

### In Scope

1. **Runtime calibration**: usar `prompt_eval_count` de Ollama para calcular `actual_tokens / estimated_tokens` ratio y ajustar el proxy dinámicamente
2. **Token ratio cache**: `_token_ratio` module-level que se auto-calibra con cada respuesta (exponential moving average)
3. **Estimator mejorado**: `estimate_tokens(text) → int` que usa el ratio calibrado en lugar de `len(text) / 4`
4. **Per-model ratios**: cache keyed por modelo (qwen3.5:9b puede tener ratio diferente a llava:7b)
5. **Observabilidad**: log del ratio actual y drift en cada calibración
6. **Fallback**: si no hay datos de calibración aún, mantener `chars/4` como default

### Out of Scope

- Integración de tokenizer externo (HuggingFace `transformers.AutoTokenizer`) — agrega ~500ms de startup y ~200MB de dependencias
- Conteo exacto pre-request (requeriría el tokenizer del modelo)
- Token budget enforcement (ya existe en `token_estimator.py`, solo mejoramos la precisión del input)

## Casos de Uso Críticos

1. **Startup frío**: primer request usa `chars/4` → Ollama responde con `prompt_eval_count=1200` para un input de 4000 chars → ratio = 1200/1000 = 1.2 → siguiente request usa `chars/3.33`
2. **Steady state**: después de ~10 requests, el EMA converge al ratio real del modelo → estimaciones con <5% de error
3. **Cambio de modelo**: usuario cambia a modelo con tokenizer diferente → cache per-model mantiene ratios separados
4. **Budget warning accuracy**: `log_context_budget()` ahora reporta warnings/errors basados en estimaciones calibradas

## Diseño Técnico

### Token ratio cache (`app/context/token_estimator.py`)

```python
_token_ratios: dict[str, float] = {}  # model_name → chars_per_token ratio
_EMA_ALPHA = 0.3  # peso del nuevo dato vs historial

def calibrate(model: str, char_count: int, actual_tokens: int) -> None:
    """Actualiza el ratio chars/token para este modelo."""
    if actual_tokens <= 0 or char_count <= 0:
        return
    observed_ratio = char_count / actual_tokens  # e.g. 3.2 chars per token
    if model in _token_ratios:
        _token_ratios[model] = _EMA_ALPHA * observed_ratio + (1 - _EMA_ALPHA) * _token_ratios[model]
    else:
        _token_ratios[model] = observed_ratio

def estimate_tokens(text: str, model: str = "default") -> int:
    """Estima tokens usando el ratio calibrado (fallback: chars/4)."""
    ratio = _token_ratios.get(model, 4.0)
    return max(1, int(len(text) / ratio))
```

### Integration point (`app/llm/client.py`)

En `chat()` y `chat_with_tools()`, después de recibir la respuesta de Ollama:

```python
if response.input_tokens and response.input_tokens > 0:
    from app.context.token_estimator import calibrate
    calibrate(model, len(prompt_text), response.input_tokens)
```

### Logging

```python
logger.debug(
    "Token calibration: model=%s ratio=%.2f (was %.2f)",
    model, new_ratio, old_ratio,
)
```

## Restricciones Arquitectónicas

- **Zero dependencies**: no agregar `transformers` ni ningún tokenizer externo
- **Zero latency**: la calibración es post-response, no agrega latencia al critical path
- **Thread-safe**: `_token_ratios` es un dict simple — OK para asyncio single-thread; si se necesita thread safety, usar `threading.Lock`
- **Backward compatible**: `estimate_tokens()` reemplaza `len(text) // 4` en los call sites existentes
- **No persistence**: el ratio se recalibra en cada restart — converge rápido (~5 requests)

## Métricas de Éxito

| Métrica | Baseline (chars/4) | Target (calibrado) |
|---|---|---|
| Error de estimación (% vs real) | ±20% | <5% después de 10 requests |
| Context overflow incidents | Desconocido | 0 (con budget warnings precisos) |
| Startup penalty | 0ms | 0ms (calibración es post-response) |
| Memory overhead | 0 | ~100 bytes per model (negligible) |
