# PRP: Security Hardening — Agent Shell & Tool Exposure

## Archivos a Modificar

| Archivo | Cambio |
|---------|--------|
| `app/skills/tools/selfcode_tools.py` | Ampliar `_SENSITIVE`; bloquear config files en `_BLOCKED_CONFIG_FILES` |
| `app/skills/tools/shell_tools.py` | Argument-level validation para `python`/`node`/`pip`; ampliar `_DANGEROUS_PATTERNS` |
| `app/security/policy_engine.py` | Default BLOCK cuando falta el archivo |
| `app/security/audit.py` | Soporte opcional de HMAC-SHA256 via `AuditTrail(log_path, hmac_key=None)` |
| `app/config.py` | Remover `pip` de `agent_shell_allowlist` default |
| `tests/test_shell_tools.py` | Nuevos tests de argumento-level validation |
| `tests/test_security.py` | Nuevos tests para HMAC audit trail y policy fail-secure |

## Analisis de Impacto en Tests Existentes

### `tests/test_shell_tools.py`

| Test existente | Afectado? | Razon |
|----------------|-----------|-------|
| `test_allow_python` → `python -m pytest` | NO | `-m` no esta bloqueado, solo `-c` y `-e` |
| `test_allow_full_path_python` → `/usr/bin/python -m pytest` | NO | idem |
| `test_deny_etc_passwd_pattern` → `cat /etc/passwd` | NO | ya es DENY por `_DANGEROUS_PATTERNS` |
| `test_deny_rm_rf_pattern` → `bash -c 'rm -rf /'` | NO | ya es DENY, no cambia |
| `test_ask_curl` → `curl https://example.com` | NO | no esta en allowlist → sigue siendo ASK |
| `TestRunCommand::test_run_command_success` → `pytest tests/` | NO | pytest es ALLOW, no cambia |
| `test_run_command_denied` → `rm -rf /` | NO | sigue siendo DENY |
| `_ALLOWLIST` en tests tiene `pip` hardcodeado | NO | es un frozenset local en el test, no usa config |

### `tests/test_security.py`

| Test existente | Afectado? | Razon |
|----------------|-----------|-------|
| `test_policy_engine_regex` | NO | crea su propio archivo YAML, no depende del default |
| `test_audit_trail_hashing` | NO | no pasa `hmac_key`; HMAC es opcional (backward compat) |

### Resto de la suite

Ningun otro test toca `selfcode_tools._SENSITIVE`, `policy_engine` default, ni `config.agent_shell_allowlist`.
Todos los tests de webhook, guardrails, tracing, memory, MCP, etc. son completamente independientes.

---

## Fases de Implementacion

### Phase 1: selfcode_tools — Sensitive fields y config files

- [ ] **1.1** Ampliar `_SENSITIVE` en `app/skills/tools/selfcode_tools.py`:
  ```python
  _SENSITIVE = {
      "whatsapp_access_token",
      "whatsapp_app_secret",
      "whatsapp_verify_token",
      "ngrok_authtoken",
      "github_token",           # NUEVO
      "langfuse_secret_key",    # NUEVO
      "langfuse_public_key",    # NUEVO
  }
  ```

- [ ] **1.2** Agregar constante `_BLOCKED_CONFIG_FILES` y usarla en `write_source_file` y `apply_patch`:
  ```python
  _BLOCKED_CONFIG_FILES = {
      "mcp_servers.json",
      "security_policies.yaml",
      "audit_trail.jsonl",
  }
  ```
  En `write_source_file` y `apply_patch`, antes de ejecutar la escritura, verificar:
  ```python
  if target.name in _BLOCKED_CONFIG_FILES:
      return f"Blocked: '{path}' is a protected configuration file."
  ```
  Nota: este check va DESPUES de `_is_safe_path` y ANTES de `target.suffix` check.

- [ ] **1.3** Verificar que `preview_patch` NO necesita el mismo check (no modifica archivos, solo preview — dejar como esta por ser read-only).

### Phase 2: shell_tools — Argument validation y dangerous patterns

- [ ] **2.1** Agregar set de flags de code-execution bloqueados en `shell_tools.py`:
  ```python
  # Flags que permiten ejecucion de codigo inline
  _CODE_EXEC_FLAGS = frozenset({"-c", "-e", "--eval", "--exec"})

  # Subcomandos peligrosos para herramientas de gestion de paquetes
  _PACKAGE_MGMT_SUBCMDS = frozenset({"install", "download", "wheel"})

  # Comandos para los que se validan argumentos peligrosos
  _CODE_EXEC_CMDS = frozenset({"python", "python3", "node", "ruby", "perl", "php"})
  _PACKAGE_MGMT_CMDS = frozenset({"pip", "pip3", "npm", "yarn"})
  ```

- [ ] **2.2** En `_validate_command`, agregar un paso ENTRE la allowlist check y el return ALLOW:
  ```python
  # 3. Allowlist → validacion adicional de argumentos
  if base_cmd in allowlist:
      # Bloquear code execution flags (-c, -e) para interpretes
      if base_cmd in _CODE_EXEC_CMDS:
          for tok in tokens[1:]:
              if tok in _CODE_EXEC_FLAGS:
                  return CommandDecision.DENY
      # Bloquear subcmds de instalacion de paquetes
      if base_cmd in _PACKAGE_MGMT_CMDS:
          if len(tokens) > 1 and tokens[1] in _PACKAGE_MGMT_SUBCMDS:
              return CommandDecision.ASK  # HITL, no DENY: puede ser legitimo
      return CommandDecision.ALLOW
  ```
  El orden queda: DENYLIST → DANGEROUS_PATTERNS → SHELL_OPERATORS → ALLOWLIST+ARG_VALIDATION → ASK.

- [ ] **2.3** Ampliar `_DANGEROUS_PATTERNS` con rutas adicionales:
  ```python
  _DANGEROUS_PATTERNS = frozenset(
      {
          "rm -rf",
          "> /dev/",
          ":()",
          "/etc/passwd",
          "/etc/shadow",
          "/etc/sudoers",    # NUEVO
          "/etc/crontab",    # NUEVO
          "/.ssh/",          # NUEVO — cubre /home/user/.ssh/ y /root/.ssh/
          "id_rsa",          # NUEVO — clave privada SSH comun
          "curl | sh",
          "curl | bash",
          "wget | sh",
          "wget | bash",
      }
  )
  ```

- [ ] **2.4** Actualizar docstring de `_validate_command` para documentar el nuevo paso.

### Phase 3: policy_engine — Fail-secure default

- [ ] **3.1** En `PolicyEngine._load_policy()`, cambiar el default cuando falta el archivo:
  ```python
  if not self.policy_path.exists():
      logger.warning(
          f"Security policy file not found at {self.policy_path}. "
          "Defaulting to BLOCK (fail-secure). Create the file to configure rules."
      )
      self._policy = BasePolicy(
          version="1.0", default_action=PolicyAction.BLOCK, rules=[]
      )
      return
  ```
  Nota: el mensaje de WARNING ya existe, solo cambia `PolicyAction.ALLOW` → `PolicyAction.BLOCK`.

- [ ] **3.2** Agregar comentario en `data/security_policies.yaml.example` (crear si no existe):
  ```yaml
  # Si este archivo no existe, el PolicyEngine rechaza TODAS las tool calls agénticas.
  # Copia este archivo a data/security_policies.yaml y ajusta las reglas.
  version: "1.0"
  default_action: "allow"
  rules: []
  ```

### Phase 4: audit — HMAC opcional

- [ ] **4.1** Modificar firma de `AuditTrail.__init__`:
  ```python
  def __init__(self, log_path: Path, hmac_key: str | None = None):
      self.log_path = log_path
      self._hmac_key = hmac_key.encode("utf-8") if hmac_key else None
      self._lock = threading.Lock()
      self._last_hash = self._initialize_log()
  ```

- [ ] **4.2** Modificar `_calculate_hash`:
  ```python
  def _calculate_hash(self, payload: dict) -> str:
      payload_str = json.dumps(payload, sort_keys=True).encode("utf-8")
      if self._hmac_key:
          import hmac as _hmac
          return _hmac.new(self._hmac_key, payload_str, hashlib.sha256).hexdigest()
      return hashlib.sha256(payload_str).hexdigest()
  ```

- [ ] **4.3** En `executor.py`, pasar `hmac_key` desde settings al crear `AuditTrail`:
  ```python
  # En get_audit_trail(), leer settings.audit_hmac_key si existe
  # Para no romper la inicializacion actual, hacer lookup lazy de settings:
  # AuditTrail(Path("data/audit_trail.jsonl"), hmac_key=os.getenv("AUDIT_HMAC_KEY"))
  ```
  Usar `os.getenv("AUDIT_HMAC_KEY")` directamente para no cambiar la firma de `get_audit_trail()`.

- [ ] **4.4** Agregar `AUDIT_HMAC_KEY` como campo opcional en `Settings`:
  ```python
  audit_hmac_key: str | None = None  # Si se setea, usa HMAC-SHA256 en el audit trail
  ```

### Phase 5: config — Remover pip del allowlist default

- [ ] **5.1** En `app/config.py`, cambiar `agent_shell_allowlist`:
  ```python
  agent_shell_allowlist: str = (
      "pytest,ruff,mypy,make,npm,git,cat,head,tail,wc,ls,find,grep,echo,python,node"
  )
  ```
  `pip` eliminado. Si alguien lo necesita, lo agrega explicitamente en `.env`.
  `python` y `node` se mantienen — el arg-level check del Phase 2 los protege.

### Phase 6: Tests nuevos

- [ ] **6.1** En `tests/test_shell_tools.py`, agregar clase `TestArgumentValidation`:

  ```python
  class TestArgumentValidation:
      """Argument-level security checks for allowlisted interpreters."""

      def test_deny_python_dash_c(self):
          assert _validate_command("python -c 'print(1)'", _ALLOWLIST) == CommandDecision.DENY

      def test_deny_python3_dash_c(self):
          assert _validate_command("python3 -c 'import os'", _ALLOWLIST) == CommandDecision.DENY

      def test_deny_node_dash_e(self):
          assert _validate_command("node -e 'console.log(1)'", _ALLOWLIST) == CommandDecision.DENY

      def test_allow_python_dash_m(self):
          # -m es seguro: importa modulo por nombre, no ejecuta string arbitrario
          assert _validate_command("python -m pytest", _ALLOWLIST) == CommandDecision.ALLOW

      def test_allow_python_script(self):
          # Ejecutar un archivo .py (sin -c) sigue siendo ALLOW
          assert _validate_command("python scripts/run_eval.py", _ALLOWLIST) == CommandDecision.ALLOW

      def test_ask_pip_install(self):
          # pip install requiere HITL
          assert _validate_command("pip install requests", _ALLOWLIST) == CommandDecision.ASK

      def test_allow_pip_show(self):
          # pip show (lectura) es ALLOW
          assert _validate_command("pip show requests", _ALLOWLIST) == CommandDecision.ALLOW

      def test_deny_ssh_key_pattern(self):
          assert _validate_command("cat /home/user/.ssh/id_rsa", _ALLOWLIST) == CommandDecision.DENY

      def test_deny_etc_sudoers(self):
          assert _validate_command("cat /etc/sudoers", _ALLOWLIST) == CommandDecision.DENY
  ```

- [ ] **6.2** En `tests/test_security.py`, agregar tests:

  ```python
  def test_policy_engine_missing_file_defaults_to_block(tmp_path):
      """PolicyEngine must fail-secure when policy file is absent."""
      engine = PolicyEngine(tmp_path / "nonexistent.yaml")
      decision = engine.evaluate("run_command", {"command": "pytest tests/"})
      assert decision.action == PolicyAction.BLOCK

  def test_audit_trail_hmac(tmp_path):
      """When hmac_key is provided, entry_hash uses HMAC-SHA256."""
      import hmac, hashlib, json
      log_file = tmp_path / "audit_hmac.jsonl"
      key = "test-secret-key"
      audit = AuditTrail(log_file, hmac_key=key)

      entry = audit.record("tool", {"x": 1}, "allow", "test", "ok")

      # Reproduce the hash manually
      payload = {
          "timestamp": entry.timestamp,
          "tool_name": "tool",
          "arguments": {"x": 1},
          "decision": "allow",
          "decision_reason": "test",
          "execution_result": "ok",
          "previous_hash": entry.previous_hash,
      }
      expected = hmac.new(
          key.encode(), json.dumps(payload, sort_keys=True).encode(), hashlib.sha256
      ).hexdigest()
      assert entry.entry_hash == expected

  def test_audit_trail_no_hmac_key_backward_compat(tmp_path):
      """Without hmac_key, AuditTrail behavior is identical to before."""
      log_file = tmp_path / "audit_plain.jsonl"
      audit = AuditTrail(log_file)  # no hmac_key
      entry1 = audit.record("t", {}, "allow", None, None)
      entry2 = audit.record("t", {}, "allow", None, None)
      assert entry2.previous_hash == entry1.entry_hash
  ```

### Phase 7: Verificacion final

- [ ] **7.1** Correr `make test` — cero fallos esperados.
- [ ] **7.2** Correr `make lint` — cero errores ruff.
- [ ] **7.3** Correr `make typecheck` — cero errores mypy.
- [ ] **7.4** Verificar manualmente que `test_allow_python` (`python -m pytest`) sigue en ALLOW.
- [ ] **7.5** Crear `data/security_policies.yaml.example` si no existe.
- [ ] **7.6** Actualizar `CLAUDE.md` con los nuevos patrones de seguridad.

---

## Mapa de Dependencias entre Fases

```
Phase 1 (selfcode)  ──┐
Phase 2 (shell)     ──┤──> Phase 6 (tests) ──> Phase 7 (verify)
Phase 3 (policy)    ──┤
Phase 4 (audit)     ──┤
Phase 5 (config)    ──┘
```

Fases 1-5 son independientes entre si — pueden implementarse en cualquier orden.
Phase 6 depende de 1-5 (los tests validan los cambios).
Phase 7 es el paso de verificacion final.

---

## Invariantes — Lo que NO Cambia

- Firma de `validate_signature()` en `webhook/security.py` — intacta.
- Firma de `run_guardrails()` — intacta.
- Todos los flows de chat normal (sin agente) — no tocan ninguno de estos archivos.
- El comportamiento de `PolicyEngine` con un archivo YAML valido — identico al actual.
- La estructura de `AuditEntry` (Pydantic model) — intacta.
- El campo `agent_shell_allowlist` en Settings — sigue siendo configurable via `.env`.
- `python -m <modulo>` sigue siendo ALLOW — uso legitimo del agente para correr pytest, etc.
