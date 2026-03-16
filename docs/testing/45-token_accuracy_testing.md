# Testing Manual: Token Accuracy — Runtime Calibration

> **Feature documentada**: [`docs/features/45-token_accuracy.md`](../features/45-token_accuracy.md)
> **Requisitos previos**: Container corriendo (`docker compose --profile dev up -d`), modelos de Ollama disponibles.

---

## Verificar que la feature está activa

La calibración es automática y no requiere activación. Después de enviar un mensaje, buscar en logs:

```bash
docker compose logs -f localforge 2>&1 | grep "token.calibration"
```

Confirmar la línea:
- `token.calibration: model=qwen3.5:9b ratio=X.XXX (was uncalibrated)` — primera calibración
- `token.calibration: model=qwen3.5:9b ratio=X.XXX (was Y.YYY)` — calibraciones sucesivas

---

## Casos de prueba principales

| Mensaje / Acción | Resultado esperado |
|---|---|
| Enviar primer mensaje al bot | Log `token.calibration: model=qwen3.5:9b ratio=... (was uncalibrated)` |
| Enviar 5+ mensajes | Ratio se estabiliza (cambios <0.1 entre requests) |
| Enviar mensaje largo (>2000 chars) | Calibración con datos más representativos, ratio converge más rápido |
| Enviar mensaje corto ("hola") | Calibración ocurre pero con datos mínimos — ratio cambia poco (EMA suaviza) |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Ollama no devuelve `prompt_eval_count` | Sin calibración — usa ratio anterior o default 4.0 |
| Restart del container | Ratios vuelven a default 4.0, reconvergen en ~5 requests |
| Modelo diferente (e.g. `llava:7b` para vision) | Ratio separado para cada modelo |
| Budget warning pre-calibración | Puede ser impreciso (±20%) — se corrige automáticamente |

---

## Verificar en logs

```bash
# Ver todas las calibraciones
docker compose logs -f localforge 2>&1 | grep "token.calibration"

# Ver budget tracking (debería reflejar estimaciones calibradas)
docker compose logs -f localforge 2>&1 | grep "context.budget"

# Comparar estimado vs real (requiere DEBUG level)
docker compose logs -f localforge 2>&1 | grep -E "token.calibration|context.budget"
```

---

## Tests automatizados

```bash
# Correr los 13 tests de calibración
.venv/bin/python -m pytest tests/test_token_calibration.py -v

# Tests cubiertos:
# - calibrate() primera llamada setea ratio directo
# - calibrate() segunda llamada aplica EMA
# - calibrate() ignora valores <= 0 (char_count y actual_tokens)
# - estimate_tokens() sin calibración usa 4.0
# - estimate_tokens() con calibración usa ratio per-model
# - Modelos diferentes mantienen ratios separados
# - get_calibration_info() retorna estado actual
# - Mínimo 1 token para texto vacío
```

---

## Verificar convergencia

Después de 10+ mensajes, verificar que el ratio se ha estabilizado:

```python
# Desde un shell Python con el app corriendo
from app.context.token_estimator import get_calibration_info
info = get_calibration_info("qwen3.5:9b")
print(f"Calibrado: {info['calibrated']}, Ratio: {info['chars_per_token']}")
# Esperado: Calibrado: True, Ratio: ~3.0-3.5 (depende del tokenizer de qwen3)
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| No aparecen logs de calibración | Log level no es DEBUG | Cambiar `LOG_LEVEL=DEBUG` en `.env` |
| Ratio no converge | Mensajes muy cortos (pocos chars) | Enviar mensajes más largos; el EMA suaviza outliers |
| Budget warnings siguen imprecisos | Pocos requests desde el restart | Esperar ~10 requests para convergencia |
| `get_calibration_info()` muestra `calibrated: False` | Ningún request completado aún | Enviar al menos un mensaje |

---

## Variables relevantes para testing

| Variable (`.env`) | Valor de test | Efecto |
|---|---|---|
| `LOG_LEVEL` | `DEBUG` | Hace visibles los logs de `token.calibration` |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Modelo cuyo ratio se calibra |
