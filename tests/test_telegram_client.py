"""Tests for TelegramClient — mocked httpx."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.telegram.client import TelegramClient


def _make_client(post_return: dict | None = None) -> tuple[TelegramClient, AsyncMock]:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = post_return or {"ok": True, "result": {"message_id": 42}}
    mock_resp.content = b"file_bytes"
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.get = AsyncMock(return_value=mock_resp)
    client = TelegramClient(mock_http, "test_token")
    return client, mock_http


def test_platform_name():
    client, _ = _make_client()
    assert client.platform_name() == "telegram"


def test_format_text_produces_html():
    client, _ = _make_client()
    result = client.format_text("**bold**")
    assert result == "<b>bold</b>"


async def test_send_message_strips_tg_prefix():
    client, mock_http = _make_client({"ok": True, "result": {"message_id": 7}})
    result = await client.send_message("tg_123456", "Hello")
    call_args = mock_http.post.call_args
    assert call_args[1]["json"]["chat_id"] == "123456"
    assert result == "7"


async def test_send_message_returns_none_on_error():
    client, mock_http = _make_client({"ok": False, "description": "Bad Request"})
    result = await client.send_message("tg_999", "Hi")
    assert result is None


async def test_send_message_uses_html_parse_mode():
    client, mock_http = _make_client()
    await client.send_message("tg_100", "text")
    call_args = mock_http.post.call_args
    assert call_args[1]["json"]["parse_mode"] == "HTML"


async def test_mark_as_read_is_noop():
    client, mock_http = _make_client()
    await client.mark_as_read("msg123")
    mock_http.post.assert_not_called()


async def test_remove_typing_indicator_is_noop():
    client, mock_http = _make_client()
    await client.remove_typing_indicator("tg_123")
    mock_http.post.assert_not_called()


async def test_send_typing_indicator_calls_sendchataction():
    client, mock_http = _make_client()
    await client.send_typing_indicator("tg_555")
    call_args = mock_http.post.call_args
    assert "sendChatAction" in call_args[0][0]
    assert call_args[1]["json"]["chat_id"] == "555"
    assert call_args[1]["json"]["action"] == "typing"


async def test_download_media():
    get_file_resp = MagicMock()
    get_file_resp.json.return_value = {
        "ok": True,
        "result": {"file_path": "photos/file_123.jpg"},
    }
    file_content_resp = MagicMock()
    file_content_resp.content = b"image_bytes"

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=get_file_resp)
    mock_http.get = AsyncMock(return_value=file_content_resp)

    client = TelegramClient(mock_http, "my_token")
    data = await client.download_media("file_id_abc")
    assert data == b"image_bytes"
    # Verify getFile was called
    post_call = mock_http.post.call_args
    assert "getFile" in post_call[0][0]
    # Verify file URL constructed correctly
    get_call = mock_http.get.call_args
    assert "my_token" in get_call[0][0]
    assert "photos/file_123.jpg" in get_call[0][0]


async def test_download_media_raises_on_error():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": False, "description": "File not found"}
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    client = TelegramClient(mock_http, "tok")
    with pytest.raises(ValueError, match="getFile error"):
        await client.download_media("bad_file_id")


async def test_set_webhook_success():
    client, mock_http = _make_client({"ok": True, "description": "Webhook was set"})
    await client.set_webhook("https://example.com/telegram/webhook", "secret123")
    call_args = mock_http.post.call_args
    assert "setWebhook" in call_args[0][0]
    assert call_args[1]["json"]["url"] == "https://example.com/telegram/webhook"
    assert call_args[1]["json"]["secret_token"] == "secret123"
