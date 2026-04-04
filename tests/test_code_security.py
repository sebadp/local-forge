"""Tests for code security pattern detection (Plan 60, Phase 2)."""

from __future__ import annotations

from app.guardrails.checks import check_code_security

# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


def test_detects_eval():
    result = check_code_security("x = eval(user_input)")
    assert not result.passed
    assert "eval()" in result.details
    assert "line 1" in result.details


def test_detects_exec():
    result = check_code_security("exec(code_string)")
    assert not result.passed
    assert "exec()" in result.details


def test_detects_os_system():
    result = check_code_security('os.system("rm -rf /")')
    assert not result.passed
    assert "os.system()" in result.details


def test_detects_os_popen():
    result = check_code_security('os.popen("ls")')
    assert not result.passed
    assert "os.popen()" in result.details


def test_detects_subprocess_shell_true():
    result = check_code_security("subprocess.call(cmd, shell=True)")
    assert not result.passed
    assert "subprocess(shell=True)" in result.details


def test_detects_pickle_load():
    result = check_code_security("pickle.loads(data)")
    assert not result.passed
    assert "pickle.load()" in result.details


def test_detects_pickle_load_singular():
    result = check_code_security("pickle.load(f)")
    assert not result.passed


def test_detects_yaml_unsafe():
    result = check_code_security("yaml.load(data)")
    assert not result.passed
    assert "yaml.load()" in result.details


def test_detects_inner_html():
    result = check_code_security("element.innerHTML = userInput")
    assert not result.passed
    assert ".innerHTML =" in result.details


def test_detects_document_write():
    result = check_code_security("document.write(payload)")
    assert not result.passed
    assert "document.write()" in result.details


def test_detects_dangerously_set_inner_html():
    result = check_code_security('<div dangerouslySetInnerHTML={{__html: data}} />')
    assert not result.passed
    assert "dangerouslySetInnerHTML" in result.details


def test_detects_new_function():
    result = check_code_security("const fn = new Function(code)")
    assert not result.passed
    assert "new Function()" in result.details


# ---------------------------------------------------------------------------
# Safe code tests
# ---------------------------------------------------------------------------


def test_passes_safe_code():
    safe = "import ast\nresult = ast.literal_eval(data)\nsubprocess.run(['ls', '-la'])"
    result = check_code_security(safe)
    assert result.passed


def test_passes_empty_content():
    result = check_code_security("")
    assert result.passed


def test_yaml_safe_load_passes():
    result = check_code_security("yaml.safe_load(data)")
    assert result.passed


# ---------------------------------------------------------------------------
# Multiple findings & line numbers
# ---------------------------------------------------------------------------


def test_multiple_findings():
    code = "x = eval(a)\ny = pickle.loads(b)"
    result = check_code_security(code)
    assert not result.passed
    assert "eval()" in result.details
    assert "pickle.load()" in result.details


def test_line_numbers_correct():
    code = "safe_line = 1\nmore_safe = 2\nx = eval(user_input)\n"
    result = check_code_security(code)
    assert not result.passed
    assert "line 3" in result.details


def test_check_name():
    result = check_code_security("safe code")
    assert result.check_name == "code_security"
