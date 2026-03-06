# PRD: Security Hardening — Agent Shell & Tool Exposure

## Objetivo y Contexto

Una auditoría de seguridad identificó vulnerabilidades en la capa de ejecucion agéntica
y en las herramientas de introspección del sistema. Las mas criticas son:

1. **Bypass de validacion de shell**: comandos allowlisteados (`python`, `node`, `pip`)
   aceptan argumentos arbitrarios que permiten ejecucion de código (`-c`, `-e`, `install`).
2. **Exposicion de secretos**: `get_runtime_config` no oculta `github_token`,
   `langfuse_secret_key` ni `langfuse_public_key`.
3. **Policy Engine fail-open**: si falta `data/security_policies.yaml`, la politica
   por defecto es ALLOW (todas las tools permitidas sin restriccion).
4. **Audit Trail sin clave HMAC**: la cadena SHA-256 no protege contra manipulacion
   deliberada por un atacante con acceso al sistema de archivos.
5. **`pip` en allowlist por defecto**: permite instalar paquetes arbitrarios en runtime.
6. **`mcp_servers.json` writable**: con `AGENT_WRITE_ENABLED=true`, el agente puede
   inyectar servidores MCP maliciosos.

Este plan resuelve todos los ítems **Critico** y **Alto** del reporte, mas algunos
**Medio**, sin romper ninguna feature existente.

## Alcance

### In Scope

- Validacion de argumentos a nivel de subcomando para `python -c`, `node -e`, `pip install`
- Ampliar `_SENSITIVE` en `selfcode_tools.py` para cubrir todos los campos de API keys
- Cambiar default del `PolicyEngine` a BLOCK cuando falta el archivo de politicas
- Agregar soporte opcional de HMAC-SHA256 al `AuditTrail` (clave via `AUDIT_HMAC_KEY` env var)
- Remover `pip` de `agent_shell_allowlist` por defecto; moverlo a ASK explicito
- Bloquear escritura en `mcp_servers.json` y `data/security_policies.yaml` desde `selfcode_tools`
- Ampliar `_DANGEROUS_PATTERNS` con rutas sensibles adicionales (`~/.ssh/`, `/etc/sudoers`, etc.)
- Tests nuevos para cada cambio; tests existentes NO deben romperse

### Out of Scope

- Rate limiter distribuido (requiere Redis, cambio arquitectural mayor)
- HITL conversation context confirmation (UX change fuera del alcance de seguridad)
- Audit log rotacion / encryption at rest
- Prompt injection desde memorias (mitigacion via system design, no filtros de texto)
- Migrar de MD5 a `secrets.token_hex` para process IDs (bajo impacto, separar en tech debt)

## Casos de Uso Criticos

1. **Agente con `AGENT_WRITE_ENABLED=true` intenta ejecutar `python -c "import os; os.remove('file')"`**
   → Debe retornar DENY (subcomando `-c` bloqueado).

2. **Usuario whitelisteado pide "show runtime config"** via chat
   → `github_token`, `langfuse_secret_key`, `langfuse_public_key` aparecen como `***hidden***`.

3. **Archivo `data/security_policies.yaml` no existe** (primer deploy)
   → `PolicyEngine` default a BLOCK, ninguna tool agéntica pasa sin regla explicita.

4. **Agente intenta escribir `data/mcp_servers.json`** via `write_source_file`
   → Operacion bloqueada, mensaje de error claro.

5. **Agente intenta `pip install requests-extra`**
   → Decision: ASK (HITL requerido), no ALLOW directo.

6. **Todos los tests existentes siguen pasando** (regresion cero).

## Restricciones Arquitectonicas

- `_validate_command` es una funcion pura (no async) — los cambios deben mantener esa propiedad
  (los tests la llaman directamente).
- `AuditTrail` no debe cambiar su firma publica si no hay HMAC key; compatibilidad hacia atras
  es obligatoria para `test_security.py::test_audit_trail_hashing`.
- `PolicyEngine` con `default_action=BLOCK` cuando falta el archivo es un cambio de comportamiento
  de produccion: documentar en `.env.example` que el archivo es requerido.
- Los tests de `test_shell_tools.py` definen su propio `_ALLOWLIST` con `python` incluido;
  los nuevos checks de argumento deben ser ortogonales al check de allowlist (no romper
  `test_allow_python` que usa `python -m pytest`).
- No agregar dependencias nuevas al proyecto.

## Orden de Prioridad de Implementacion

| # | Cambio | Severidad | Riesgo de Regresion |
|---|--------|-----------|---------------------|
| 1 | Ampliar `_SENSITIVE` (selfcode_tools) | Critico | Nulo |
| 2 | Bloquear archivos de config en `selfcode_tools` write | Alto | Nulo |
| 3 | Argument-level validation para python/node/pip (shell_tools) | Critico | Bajo |
| 4 | Ampliar `_DANGEROUS_PATTERNS` (shell_tools) | Alto | Nulo |
| 5 | Policy Engine fail-secure (policy_engine) | Alto | Nulo |
| 6 | HMAC opcional en AuditTrail | Alto | Nulo |
| 7 | Remover `pip` de allowlist por defecto (config) | Alto | Nulo |
| 8 | Tests nuevos para todos los cambios | — | — |
