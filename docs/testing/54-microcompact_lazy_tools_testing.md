# Testing Manual: MicroCompact & Lazy Tool Loading

> **Feature documentada**: [`docs/features/54-microcompact_lazy_tools.md`](../features/54-microcompact_lazy_tools.md)
> **Requisitos previos**: Container corriendo (`docker compose up -d`), Ollama disponible.

---

## Verificar que la feature está activa

MicroCompact y discover_tools están siempre activos (no tienen feature flag).

```bash
# Verificar que discover_tools está registrado
docker compose logs -f localforge | grep -i "discover_tools"
```

---

## Casos de prueba: MicroCompact

| Mensaje / Acción | Resultado esperado |
|---|---|
| Enviar un mensaje que requiera múltiples tool calls (ej: `/agent investigá el clima en 3 ciudades`) | Después de 2+ rounds de tools, los tool results viejos se reemplazan con `[Tool result cleared — N chars]` |
| Enviar un mensaje simple que use 1 tool (ej: `cuánto es 5*3`) | Tool result corto (<200 chars) NO se compacta |
| Sesión con 5+ tool calls en secuencia | Los primeros tool results se compactan, los últimos 2 rounds permanecen intactos |

### Verificar en logs

```bash
# Buscar actividad de microcompact
docker compose logs -f localforge 2>&1 | grep -i "microcompact"

# Ver clearing de tool results
docker compose logs -f localforge 2>&1 | grep -i "cleared.*chars"
```

---

## Casos de prueba: discover_tools

| Mensaje / Acción | Resultado esperado |
|---|---|
| Enviar un mensaje que necesite un tool no cargado inicialmente (ej: `buscá archivos .py en el proyecto`) | El LLM llama `discover_tools(query="files")`, ve las tools disponibles, luego llama `request_more_tools` |
| Enviar `qué tools tenés disponibles?` durante una sesión de agent | El LLM puede usar `discover_tools` para listar tools por keyword |

### Verificar en logs

```bash
docker compose logs -f localforge 2>&1 | grep -i "discover_tools\|search_tools"
```

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| Tool result exactamente 200 chars | NO se compacta (umbral es >200) |
| Tool result de tool NO en `COMPACTABLE_TOOLS` | NO se compacta aunque sea viejo y largo |
| `discover_tools` con query sin resultados | Retorna lista vacía, LLM maneja gracefully |
| Round actual (no viejo) | Nunca se compacta, independientemente del tamaño |

---

## Tests automatizados

```bash
.venv/bin/python -m pytest tests/test_microcompact.py tests/test_meta_tools.py -v
# 17 tests: compaction logic, tool search, edge cases
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| Context overflow en sesiones largas | `max_age_rounds` muy alto | Default es 2 — verificar que no fue modificado |
| LLM no encuentra tools | `discover_tools` no registrado | Verificar startup logs, tools/__init__.py |
| Tool results no se compactan | Tool no está en `COMPACTABLE_TOOLS` | Revisar set en `microcompact.py` |

---

## Variables relevantes para testing

| Variable | Valor de test | Efecto |
|---|---|---|
| `COMPACTABLE_TOOLS` (constante en código) | Set de 16 tools | Tools cuyos resultados se compactan |
| `max_age_rounds` (param) | `2` | Rounds de antigüedad antes de compactar |
| `_MIN_CONTENT_LEN` (constante) | `200` | Resultados más cortos no se compactan |
