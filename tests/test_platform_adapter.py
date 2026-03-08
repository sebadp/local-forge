"""Tests for WhatsAppPlatformAdapter and PlatformClient Protocol compliance."""

from unittest.mock import AsyncMock, MagicMock

from app.platforms.base import PlatformClient
from app.webhook.router import WhatsAppPlatformAdapter
from app.whatsapp.client import WhatsAppClient


def _make_wa_client() -> WhatsAppClient:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"messages": [{"id": "wamid.test"}]}
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.get = AsyncMock(return_value=mock_resp)
    return WhatsAppClient(
        http_client=mock_http,
        access_token="test_token",
        phone_number_id="123456",
    )


def test_adapter_satisfies_protocol():
    wa = _make_wa_client()
    adapter = WhatsAppPlatformAdapter(wa, "msg123")
    assert isinstance(adapter, PlatformClient)


def test_platform_name():
    wa = _make_wa_client()
    adapter = WhatsAppPlatformAdapter(wa, "msg123")
    assert adapter.platform_name() == "whatsapp"


def test_format_text_converts_markdown():
    wa = _make_wa_client()
    adapter = WhatsAppPlatformAdapter(wa)
    result = adapter.format_text("**bold**")
    assert result == "*bold*"


async def test_send_typing_indicator_calls_send_reaction():
    wa = _make_wa_client()
    wa.send_reaction = AsyncMock()
    adapter = WhatsAppPlatformAdapter(wa, "msg456")
    await adapter.send_typing_indicator("5491112345678")
    wa.send_reaction.assert_called_once_with("msg456", "5491112345678", "⏳")


async def test_send_typing_indicator_no_op_without_msg_id():
    wa = _make_wa_client()
    wa.send_reaction = AsyncMock()
    adapter = WhatsAppPlatformAdapter(wa)  # no message_id
    await adapter.send_typing_indicator("5491112345678")
    wa.send_reaction.assert_not_called()


async def test_remove_typing_indicator_uses_stored_msg_id():
    wa = _make_wa_client()
    wa.send_reaction = AsyncMock()
    adapter = WhatsAppPlatformAdapter(wa, "msg789")
    await adapter.remove_typing_indicator("5491112345678")
    wa.send_reaction.assert_called_once_with("msg789", "5491112345678", "")


async def test_remove_typing_indicator_uses_provided_id():
    wa = _make_wa_client()
    wa.send_reaction = AsyncMock()
    adapter = WhatsAppPlatformAdapter(wa, "stored_id")
    await adapter.remove_typing_indicator("5491112345678", indicator_id="override_id")
    wa.send_reaction.assert_called_once_with("override_id", "5491112345678", "")


async def test_mark_as_read_delegates():
    wa = _make_wa_client()
    wa.mark_as_read = AsyncMock()
    adapter = WhatsAppPlatformAdapter(wa, "msgXYZ")
    await adapter.mark_as_read("msgXYZ")
    wa.mark_as_read.assert_called_once_with("msgXYZ")


async def test_send_message_delegates():
    wa = _make_wa_client()
    wa.send_message = AsyncMock(return_value="wamid.returned")
    adapter = WhatsAppPlatformAdapter(wa, "msg000")
    result = await adapter.send_message("5491112345678", "Hello")
    wa.send_message.assert_called_once_with("5491112345678", "Hello")
    assert result == "wamid.returned"
