# PRP: Token Accuracy — Runtime Calibration

> **PRD**: [`45-token_accuracy_prd.md`](45-token_accuracy_prd.md)

## Archivos a Modificar

- `app/context/token_estimator.py`: Agregar `calibrate()`, `_token_ratios`, `_EMA_ALPHA`; modificar `estimate_tokens()` para usar ratio calibrado per-model
- `app/llm/client.py`: Llamar `calibrate()` en `chat_with_tools()` después de recibir respuesta con `prompt_eval_count`
- `tests/test_token_calibration.py`: Tests unitarios de calibración, EMA, fallback, per-model

## Fases de Implementación

### Phase 1: Calibration core en `token_estimator.py`
- [x] Agregar `_token_ratios: dict[str, float]` y `_EMA_ALPHA = 0.3` module-level
- [x] Implementar `calibrate(model, char_count, actual_tokens)` con EMA
- [x] Modificar `estimate_tokens(text, model)` para usar ratio calibrado (fallback 4.0)
- [x] Agregar `get_calibration_info(model) -> dict` para observabilidad
- [x] Log debug en cada calibración con old/new ratio

### Phase 2: Integration en `client.py`
- [x] En `chat_with_tools()`, después de extraer `input_tokens`, llamar `calibrate()` con el char count del prompt
- [x] Calcular `char_count` como suma de chars de todos los messages del payload

### Phase 3: Actualizar call sites
- [x] `estimate_tokens()` en `estimate_messages_tokens()` — pasar model (default OK)
- [x] `estimate_sections()` — aceptar `model` param opcional
- [x] `log_context_budget()` — aceptar `model` param opcional, propagar a `estimate_messages_tokens`
- [x] Router call site en `router.py` — pasar model del settings al log_context_budget

### Phase 4: Tests
- [x] Test: `calibrate()` primera llamada setea ratio directo
- [x] Test: `calibrate()` segunda llamada aplica EMA
- [x] Test: `calibrate()` ignora valores <= 0
- [x] Test: `estimate_tokens()` sin calibración usa 4.0
- [x] Test: `estimate_tokens()` con calibración usa ratio per-model
- [x] Test: modelos diferentes mantienen ratios separados
- [x] Test: `get_calibration_info()` retorna estado actual
- [x] Correr `make check` — 752 passed, lint OK, mypy OK

### Phase 5: Documentación
- [x] Actualizar `CLAUDE.md` con patrón de token calibration
- [x] Actualizar `docs/exec-plans/README.md` con estado del plan
