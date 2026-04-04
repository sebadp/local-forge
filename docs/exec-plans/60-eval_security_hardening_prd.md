# PRD: Eval & Security Hardening — Multi-Criteria Judge, Code Security Patterns, Credential Scrubbing (Plan 60)

## Objetivo y Contexto

### Problema

El informe comparativo `EX-claude_code_guardrails_evals_telemetry.md` identificó 3 gaps de alto impacto entre Claude Code y LocalForge que son accionables con esfuerzo bajo-medio:

**G1 — LLM-as-judge simplista (impacto: alto)**

El judge actual (`run_quick_eval` y `check_tool_coherence`) usa un prompt binary yes/no:

```
"Does the actual answer correctly and completely answer the question? Reply ONLY 'yes' or 'no'."
```

Esto tiene 3 problemas:
1. **No captura dimensiones de calidad**: una respuesta puede ser correcta pero verbosa, o completa pero con code smells. El binary pass/fail pierde información.
2. **No es calibrable**: no hay rubric contra la cual calibrar. Dos evaluaciones del mismo trace pueden dar resultados distintos sin forma de diagnosticar por qué.
3. **No genera explicación**: cuando falla, solo dice "LLM judged reply incoherent". No dice qué está mal ni cómo mejorar.

Claude Code internamente usa multi-criteria judging (correctness, completeness, conciseness, tool_usage) con calibración contra juicio humano. Su principio: "nunca confiar en scores hasta leer transcripts".

**G3 — No hay detección de code security patterns en output (impacto: medio)**

Cuando el agente genera código (via `write_source_file` o `apply_patch`), no se verifica que el código generado no contenga patterns de seguridad peligrosos. Claude Code tiene un hook `security_reminder_hook.py` que detecta:
- `eval()` / `exec()` / `os.system()` / `subprocess.call(shell=True)` (code injection)
- `dangerouslySetInnerHTML` / `document.write` / `.innerHTML` (XSS)
- `pickle.loads()` / `yaml.load()` sin SafeLoader (deserialization)
- SQL string concatenation (SQL injection)
- GitHub Actions `${{ }}` en `run:` (command injection)

LocalForge genera código via el agente y lo escribe a disco sin ningún check de seguridad sobre el contenido.

**G5 — No hay credential scrubbing en subprocesos (impacto: medio)**

`shell_tools.py` ejecuta subprocesos sin limpiar el environment:
```python
proc = await asyncio.create_subprocess_exec(
    *tokens, stdout=PIPE, stderr=PIPE, stdin=DEVNULL, cwd=str(_PROJECT_ROOT),
    # NO hay env= parameter → hereda TODO el environment
)
```

Esto expone `WHATSAPP_ACCESS_TOKEN`, `GITHUB_TOKEN`, `LANGFUSE_SECRET_KEY`, etc. a cualquier comando que el agente ejecute. Si el agente ejecuta `curl` o un script malicioso, esas credenciales están accesibles via `$WHATSAPP_ACCESS_TOKEN`.

Claude Code implementa `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` que limpia credenciales de subprocesos.

### Por qué estos 3 juntos

- Los 3 son mejoras de seguridad/calidad que no cambian la arquitectura
- No tienen dependencias entre sí — se pueden implementar en paralelo
- El credential scrubbing es un fix de seguridad que debería haberse hecho antes
- El multi-criteria judge mejora toda la pipeline de evals
- El code security check previene que el agente introduzca vulnerabilidades

## Alcance

### In Scope

#### A. Multi-Criteria LLM-as-Judge

Reemplazar el judge binary por un judge multi-criteria con rubric explícita y scoring numérico.

**Criterios:**
| Criterio | Descripción | Score |
|----------|-------------|-------|
| `correctness` | ¿La respuesta es factualmente correcta y resuelve el problema? | 0.0 - 1.0 |
| `completeness` | ¿Cubre todos los aspectos de la pregunta? | 0.0 - 1.0 |
| `conciseness` | ¿Es concisa sin perder información? (penaliza verbosidad excesiva) | 0.0 - 1.0 |
| `tool_usage` | ¿Usó las herramientas correctas de forma eficiente? (solo si aplica) | 0.0 - 1.0 |

**Formato de respuesta del judge:**
```json
{
  "correctness": 0.8,
  "completeness": 0.9,
  "conciseness": 0.6,
  "tool_usage": 1.0,
  "reasoning": "Correct answer but overly verbose — repeated the same information in different words."
}
```

**Integración:**
- `run_quick_eval()` usa el nuevo judge y reporta scores por criterio
- `check_tool_coherence()` usa una versión simplificada (correctness + completeness)
- Los scores individuales se graban como `system:correctness`, `system:completeness`, etc. en `add_score()`
- El dataset curation (`maybe_curate_to_dataset`) usa el promedio de los criterios en vez del binary pass/fail

#### B. Code Security Pattern Detection

Nuevo guardrail check determinístico `check_code_security(content)` que escanea contenido escrito a archivos:

**Patterns a detectar:**
| Pattern | Regex | Riesgo |
|---------|-------|--------|
| Python code injection | `eval(`, `exec(`, `os.system(`, `subprocess.call(.*shell=True` | Ejecución arbitraria |
| Python deserialization | `pickle.loads(`, `pickle.load(`, `yaml.load(` sin `Loader=` | Deserialización insegura |
| JavaScript injection | `eval(`, `new Function(`, `document.write(`, `.innerHTML =` | XSS / code injection |
| React XSS | `dangerouslySetInnerHTML` | XSS |
| SQL injection | String concatenation con `SELECT`, `INSERT`, `UPDATE`, `DELETE` + `f"` o `%` o `+` | SQL injection |
| Shell injection | `os.popen(`, `commands.getoutput(` | Command injection |

**Integración:**
- Se ejecuta como post-check en `write_source_file` y `apply_patch` (después de escribir)
- Retorna warning al LLM (no bloquea) — el LLM puede decidir si es un false positive
- Se registra en el audit trail
- Nuevo guardrail score: `system:code_security` (1.0 = clean, 0.0 = patterns detectados)

#### C. Credential Scrubbing en Subprocesos

Limpiar credenciales del environment antes de pasar a `subprocess_exec`:

**Variables a scrubear:**
```python
_SCRUB_PATTERNS = {
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
    "GITHUB_TOKEN",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "AUDIT_HMAC_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "NGROK_AUTHTOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}
```

Además, scrubear cualquier variable cuyo nombre contenga `_SECRET`, `_TOKEN`, `_KEY`, `_PASSWORD`, `_CREDENTIAL` (pattern matching).

**Mecanismo:** Crear `_scrubbed_env()` que retorna `os.environ.copy()` con las variables sensibles eliminadas. Pasarlo como `env=` a `create_subprocess_exec()`.

### Out of Scope

- **Hooks system completo (tipo Claude Code)**: Requiere diseño arquitectónico significativo. Plan futuro dedicado.
- **A/B testing de prompts**: Requiere harness de comparación. Planificable una vez que el judge multi-criteria esté en producción.
- **Sandbox OS-level**: macOS sandbox / Linux namespaces. Demasiado complejo para este plan.
- **PostToolUse validation genérica**: Solo implementamos code security check (subconjunto más valioso).
- **OTEL export**: Langfuse es suficiente. Baja prioridad.

## Casos de Uso Críticos

1. **Multi-criteria eval de respuesta de código**:
   - Usuario: "creá un endpoint /users con paginación"
   - Agente genera código correcto pero con 200 líneas de comentarios innecesarios
   - Judge: `correctness=0.9, completeness=1.0, conciseness=0.3, tool_usage=0.8`
   - Score promedio: 0.75. Se curate como golden candidate (no failure, no confirmed golden)
   - `reasoning`: "Code is correct and complete but excessively commented."

2. **Code security detection en write**:
   - Agente escribe `app/utils.py` que contiene `eval(user_input)`
   - `check_code_security()` detecta el pattern
   - Warning al LLM: "⚠️ Security: detected `eval()` in utils.py (line 15). Consider using `ast.literal_eval()` or a safer alternative."
   - LLM corrige y reescribe con `ast.literal_eval()`

3. **Credential scrubbing**:
   - Agente ejecuta `curl https://api.example.com -H "Authorization: Bearer $GITHUB_TOKEN"`
   - Antes: `GITHUB_TOKEN` disponible → credencial enviada a api.example.com
   - Después: `GITHUB_TOKEN` scrubbed → curl falla con auth error → agente no puede filtrar credenciales

4. **Judge con reasoning para diagnóstico**:
   - Eval falla en `conciseness` para la categoría "weather"
   - Operador lee el `reasoning`: "Responses include unnecessary disclaimers about data accuracy."
   - Operador ajusta el prompt del weather skill para ser más directo

## Restricciones Arquitectónicas

- **Multi-criteria judge usa `think=False`**: Obligatorio para prompts JSON. El judge retorna JSON parseado.
- **Code security check es determinístico**: Sin LLM. Regex puro. No bloquea, solo advierte.
- **Credential scrubbing no afecta al proceso padre**: Solo scrubea el env del subprocess.
- **Backward compatibility del judge**: `run_quick_eval()` sigue retornando un accuracy %. Los scores por criterio son metadata adicional.
- **Fail-open**: Todos los nuevos checks son fail-open. Si el JSON del judge no parsea, se cae a binary yes/no. Si el regex de code security falla, se loguea y no bloquea.
