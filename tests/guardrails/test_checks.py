"""Unit tests for individual guardrail checks."""

from app.guardrails.checks import (
    check_excessive_length,
    check_hallucination,
    check_language_match,
    check_no_pii,
    check_no_raw_tool_json,
    check_not_empty,
    check_tool_coherence,
    redact_pii,
)


class TestCheckNotEmpty:
    def test_passes_for_normal_reply(self):
        result = check_not_empty("Hola, ¿cómo estás?")
        assert result.passed is True
        assert result.check_name == "not_empty"

    def test_fails_for_empty_string(self):
        result = check_not_empty("")
        assert result.passed is False

    def test_fails_for_whitespace_only(self):
        result = check_not_empty("   \n\t  ")
        assert result.passed is False

    def test_has_latency(self):
        result = check_not_empty("hello")
        assert result.latency_ms >= 0


class TestCheckExcessiveLength:
    def test_passes_for_normal_reply(self):
        result = check_excessive_length("A" * 500)
        assert result.passed is True

    def test_passes_at_limit(self):
        result = check_excessive_length("A" * 8000)
        assert result.passed is True

    def test_fails_above_limit(self):
        result = check_excessive_length("A" * 8001)
        assert result.passed is False
        assert "8001" in result.details

    def test_check_name(self):
        result = check_excessive_length("hello")
        assert result.check_name == "excessive_length"


class TestCheckNoRawToolJson:
    def test_passes_for_normal_reply(self):
        result = check_no_raw_tool_json("The weather is sunny today.")
        assert result.passed is True

    def test_fails_with_raw_tool_json(self):
        result = check_no_raw_tool_json('Here is the result: {"tool_call": "get_weather"}')
        assert result.passed is False

    def test_case_insensitive(self):
        result = check_no_raw_tool_json('{"Tool_Call": "something"}')
        assert result.passed is False

    def test_check_name(self):
        result = check_no_raw_tool_json("hello")
        assert result.check_name == "no_raw_tool_json"


class TestCheckNoPii:
    def test_passes_for_clean_reply(self):
        result = check_no_pii("Cuéntame más sobre el proyecto.", "Claro, con gusto te ayudo.")
        assert result.passed is True

    def test_passes_when_email_is_in_user_text(self):
        # If user provided the email, bot can repeat it
        user = "Mi email es test@example.com, ¿puedes confirmar?"
        reply = "Tu email es test@example.com, confirmado."
        result = check_no_pii(user, reply)
        assert result.passed is True

    def test_fails_when_bot_generates_email(self):
        user = "¿Cuál es mi email?"
        reply = "Tu email es usuario@empresa.com"
        result = check_no_pii(user, reply)
        assert result.passed is False
        assert "email" in result.details

    def test_fails_when_bot_generates_token(self):
        user = "Dame mi token"
        reply = "Tu token es sk-abc123456789012345678901234567890"
        result = check_no_pii(user, reply)
        assert result.passed is False

    def test_check_name(self):
        result = check_no_pii("hola", "hola")
        assert result.check_name == "no_pii"


class TestCheckLanguageMatch:
    async def test_skips_when_user_text_too_short(self):
        result = await check_language_match("Hi", "Hola, ¿cómo puedo ayudarte hoy?")
        assert result.passed is True
        assert "skipped" in result.details

    async def test_skips_when_reply_too_short(self):
        result = await check_language_match("Como estas hoy amigo?", "Hi")
        assert result.passed is True
        assert "skipped" in result.details

    async def test_skips_when_both_too_short(self):
        result = await check_language_match("How are you?", "Fine")
        assert result.passed is True
        assert "skipped" in result.details

    async def test_passes_for_matching_languages(self, mocker):
        mocker.patch("langdetect.detect", return_value="es")
        user = "Hola, necesito ayuda con mi proyecto de investigación"
        reply = "Por supuesto, estaré encantado de ayudarte con tu proyecto"
        result = await check_language_match(user, reply)
        assert result.passed is True

    async def test_skips_for_url_input(self):
        url = "https://acrobat.adobe.com/id/urn:aaid:sc:US:bd1137eb-32a1-498f-b5ad-c1aaeb244814"
        reply = "La URL que proporcionaste parece ser un identificador único para un documento en Adobe."
        result = await check_language_match(url, reply)
        assert result.passed is True
        assert "non-natural" in result.details

    async def test_check_name(self):
        result = await check_language_match("short", "also short")
        assert result.check_name == "language_match"


class TestCheckToolCoherence:
    async def test_passes_when_llm_says_yes(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.return_value = "yes"
        result = await check_tool_coherence("¿Qué hora es?", "Son las 3pm.", mock_client)
        assert result.passed is True
        assert result.check_name == "tool_coherence"

    async def test_fails_when_llm_says_no(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.return_value = "no"
        result = await check_tool_coherence("¿Qué hora es?", "El cielo es azul.", mock_client)
        assert result.passed is False
        assert "incoherent" in result.details

    async def test_fails_open_on_exception(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.side_effect = RuntimeError("boom")
        result = await check_tool_coherence("hello", "hi", mock_client)
        assert result.passed is True
        assert "check error" in result.details

    async def test_check_name(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.return_value = "yes"
        result = await check_tool_coherence("q", "a", mock_client)
        assert result.check_name == "tool_coherence"


class TestCheckHallucination:
    async def test_passes_when_llm_says_no(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.return_value = "no"
        result = await check_hallucination("¿Capital de Francia?", "Es París.", mock_client)
        assert result.passed is True
        assert result.check_name == "hallucination_check"

    async def test_fails_when_llm_says_yes(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.return_value = "yes"
        result = await check_hallucination(
            "¿Quién ganó la Copa del Mundo 2030?",
            "La ganó el equipo de Marte en el partido final.",
            mock_client,
        )
        assert result.passed is False
        assert "hallucination" in result.details

    async def test_fails_open_on_exception(self, mocker):
        mock_client = mocker.AsyncMock()
        mock_client.chat.side_effect = RuntimeError("boom")
        result = await check_hallucination("hello", "hi", mock_client)
        assert result.passed is True
        assert "check error" in result.details


class TestRedactPii:
    def test_redacts_email(self):
        text = "Contacta a user@example.com para más info"
        redacted = redact_pii(text)
        assert "user@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted

    def test_redacts_bearer_token(self):
        text = "Usa el header: Bearer abc123def456ghi789jkl"
        redacted = redact_pii(text)
        assert "Bearer abc123" not in redacted
        assert "[REDACTED_TOKEN]" in redacted

    def test_preserves_non_pii(self):
        text = "El clima hoy es soleado y cálido."
        redacted = redact_pii(text)
        assert redacted == text
