# Testing Manual: Memoria Avanzada

> **Feature documentada**: [`docs/features/07-memoria_avanzada.md`](../features/07-memoria_avanzada.md)
> **Requisitos previos**: Container corriendo.

---

## Casos de prueba principales

| Acción | Resultado esperado |
|---|---|
| `/remember Mi cumpleaños es el 15 de marzo` | Memoria guardada en DB + MEMORY.md actualizado |
| Editar `data/MEMORY.md` manualmente | Watcher detecta cambio y sincroniza con DB |
| Enviar 25+ mensajes para forzar summarization | Pre-compaction flush extrae facts y eventos antes de resumir |
| `/clear` | Snapshot guardado en `data/memory/snapshots/` |
| `/remember Mi cumpleaños es el 15 de Marzo` (duplicado) | Dedup detecta similitud — no guarda duplicado |

---

## Verificar en logs

```bash
# Sync MEMORY.md
docker compose logs -f localforge 2>&1 | grep -i "memory.*sync\|watcher"

# Pre-compaction flush
docker compose logs -f localforge 2>&1 | grep -i "flush\|pre.compaction\|extract"

# Consolidation
docker compose logs -f localforge 2>&1 | grep -i "consolidat\|dedup\|merge"

# Daily log
docker compose logs -f localforge 2>&1 | grep -i "daily.log"
```

---

## Queries de verificación en DB

```bash
# Memorias por categoría
sqlite3 data/localforge.db "SELECT category, COUNT(*) FROM memories WHERE active=1 GROUP BY category;"

# Daily logs en disco
ls -la data/memory/

# Snapshots
ls -la data/memory/snapshots/
```

---

## Troubleshooting

| Problema | Causa probable | Solución |
|---|---|---|
| MEMORY.md no se actualiza | Watcher no iniciado | Verificar `MEMORY_FILE_WATCH_ENABLED=true` y que `watchdog` esté instalado |
| Loop de sync DB↔archivo | Guard anti-loop roto | Reiniciar container, verificar logs de watcher |
| Facts no se extraen en flush | Modelo LLM muy limitado | Verificar que el modelo en uso es `qwen3.5:9b` |
