# Testing Manual: Skills y Herramientas

> **Feature documentada**: [`docs/features/06-skills_herramientas.md`](../features/06-skills_herramientas.md)
> **Requisitos previos**: Container corriendo, skills configurados.

---

## Casos de prueba principales

| Mensaje | Resultado esperado |
|---|---|
| `¿Qué hora es?` | Responde con la hora actual usando `get_current_datetime` |
| `Cuánto es 2^10 * 3.14?` | Responde `3215.36` usando calculator |
| `Guardá una nota: revisar PR mañana` | Crea la nota y confirma |
| `Mostrá mis notas` | Lista notas guardadas |
| `/review-skill` | Lista skills + MCP servers activos |
| `¿Qué versión sos?` | Responde usando selfcode tools |

---

## Edge cases y validaciones

| Escenario | Resultado esperado |
|---|---|
| `Calculá import os` | Rechazado por el AST validator |
| Tool call con parámetros faltantes | Error graceful, no crash |
| MCP server caído | Las demás tools siguen funcionando |
| 5 tool calls consecutivos sin respuesta | Loop se detiene y retorna último estado |

---

## Verificar en logs

```bash
# Clasificación de intent
docker compose logs -f localforge 2>&1 | grep -i "Tool router\|categories"

# Tool calls
docker compose logs -f localforge 2>&1 | grep -i "Tool iteration\|tool call\|_run_tool"

# MCP
docker compose logs -f localforge 2>&1 | grep -i "mcp\|server"
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| LLM no llama tools | Modelo no soporta tool calling o tools no registrados | Verificar `ollama show qwen3.5:9b` y logs de startup |
| Calculator da error | Expresión no está en la whitelist AST | Verificar `calculator_tools.py` |
| "No tools available" | Skills dir incorrecto o SKILL.md mal formateado | Verificar `SKILLS_DIR` y format del frontmatter |
| MCP tool no aparece | Server no se conectó correctamente | Verificar `mcp_servers.json` y logs de MCP |
