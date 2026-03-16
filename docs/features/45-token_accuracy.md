# Feature: Token Accuracy — Runtime Calibration

> **Versión**: v1.0
> **Fecha de implementación**: 2026-03-12
> **Exec Plan**: 45
> **Estado**: ✅ Implementada

---

## ¿Qué hace?

Mejora la precisión de la estimación de tokens del proxy `chars/4` calibrándola automáticamente en runtime con los conteos reales que Ollama devuelve en cada respuesta. Después de ~10 requests, el error baja de ±20% a <5%.

---

## Arquitectura

```
[User message]
       │
       ▼
[OllamaClient.chat_with_tools()]
       │
       ├──► Ollama API ──► response.prompt_eval_count (tokens reales)
       │
       ▼
[calibrate(model, char_count, actual_tokens)]
       │
       ▼
[_token_ratios[model] = EMA(observed, previous)]
       │
       ▼
[estimate_tokens(text, model)] ◄── usa ratio calibrado
       │
       ▼
[log_context_budget()] ──► WARNING/ERROR más precisos
```

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `app/context/token_estimator.py` | Calibración EMA, estimación per-model, budget logging |
| `app/llm/client.py` | Trigger de calibración post-response |
| `app/webhook/router.py` | Call site que pasa `model` al budget tracking |
| `tests/test_token_calibration.py` | 13 tests unitarios |

---

## Walkthrough técnico: cómo funciona

1. **Respuesta de Ollama**: `chat_with_tools()` recibe `prompt_eval_count` (tokens reales de input) → `client.py:96`
2. **Cálculo de chars**: suma de `len(content)` de todos los messages del payload → `client.py:100`
3. **Calibración**: `calibrate(model, char_count, actual_tokens)` calcula `observed_ratio = chars / tokens` y actualiza `_token_ratios[model]` con EMA (α=0.3) → `token_estimator.py:29`
4. **Estimación**: `estimate_tokens(text, model)` usa el ratio calibrado en lugar del default 4.0 → `token_estimator.py:53`
5. **Budget tracking**: `log_context_budget()` en router.py pasa `settings.ollama_model` para usar estimaciones calibradas → `router.py:1441`

---

## Cómo extenderla

- **Cambiar velocidad de convergencia**: modificar `_EMA_ALPHA` en `token_estimator.py` (0.3 = convergencia moderada, >0.5 = más rápido pero más ruidoso)
- **Persistir ratios entre restarts**: serializar `_token_ratios` a archivo/DB en shutdown y cargar en startup (actualmente no persistido — converge rápido)
- **Agregar modelo nuevo**: automático — cada modelo obtiene su propio ratio al primer request

---

## Guía de testing

→ Ver [`docs/testing/45-token_accuracy_testing.md`](../testing/45-token_accuracy_testing.md)

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| EMA con α=0.3 | Media aritmética simple | EMA se adapta a drift del tokenizer y pesa más los datos recientes |
| Per-model ratios | Ratio global único | qwen3.5:9b y llava:7b tienen tokenizers diferentes (BPE vs otro) |
| Calibración post-response | Tokenizer externo (HuggingFace) | Zero dependencies, zero latency — el dato ya viene gratis de Ollama |
| No persistir ratios | SQLite/archivo | Converge en ~5-10 requests, no justifica la complejidad |
| `model` param con default `"default"` | Romper firma existente | Backward compatible — call sites sin model siguen funcionando |

---

## Gotchas y edge cases

- **Primer request**: usa `chars/4` (default) — no hay datos de calibración aún. La primera respuesta calibra inmediatamente
- **Modelo nunca visto**: usa el default 4.0. Se calibra al primer request con ese modelo
- **`prompt_eval_count` ausente**: Ollama puede omitirlo en errores — `calibrate()` ignora valores ≤0
- **Thread safety**: `_token_ratios` es un dict simple — OK para asyncio single-thread (Python GIL). Si se usa con threads, agregar `threading.Lock`
- **Restart**: los ratios se pierden y reconvergen. No es un problema en la práctica (5-10 requests)

---

## Variables de configuración relevantes

| Variable (`config.py`) | Default | Efecto |
|---|---|---|
| `ollama_model` | `qwen3.5:9b` | Modelo usado como key en `_token_ratios` para budget tracking |

No hay variables nuevas — la feature es automática y sin configuración.
