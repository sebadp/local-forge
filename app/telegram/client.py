from __future__ import annotations

import logging

import httpx

from app.formatting.splitter import split_message

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_TELEGRAM_FILE = "https://api.telegram.org/file/bot{token}/{path}"


class TelegramClient:
    """Telegram Bot API client that implements the PlatformClient protocol."""

    def __init__(self, http_client: httpx.AsyncClient, token: str) -> None:
        self._http = http_client
        self._token = token

    def _url(self, method: str) -> str:
        return _TELEGRAM_API.format(token=self._token, method=method)

    async def send_message(self, chat_id: str, text: str) -> str | None:
        """Send a message to a chat. chat_id may have 'tg_' prefix (stripped)."""
        cid = chat_id.removeprefix("tg_")
        chunks = split_message(text)
        last_id: str | None = None
        for chunk in chunks:
            try:
                resp = await self._http.post(
                    self._url("sendMessage"),
                    json={
                        "chat_id": cid,
                        "text": chunk,
                        "parse_mode": "HTML",
                    },
                )
                data = resp.json()
                if data.get("ok"):
                    last_id = str(data["result"]["message_id"])
                else:
                    logger.warning("Telegram sendMessage error: %s", data)
            except Exception:
                logger.exception("Telegram sendMessage failed")
        return last_id

    async def download_media(self, file_id: str) -> bytes:
        """Download file bytes by Telegram file_id."""
        resp = await self._http.post(
            self._url("getFile"),
            json={"file_id": file_id},
        )
        data = resp.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram getFile error: {data}")
        file_path = data["result"]["file_path"]
        url = _TELEGRAM_FILE.format(token=self._token, path=file_path)
        file_resp = await self._http.get(url)
        file_resp.raise_for_status()
        return file_resp.content

    async def mark_as_read(self, message_id: str) -> None:
        """No-op: Telegram has no read receipts."""

    async def send_typing_indicator(self, chat_id: str) -> None:
        """Send a typing action (auto-expires ~5s)."""
        cid = chat_id.removeprefix("tg_")
        try:
            await self._http.post(
                self._url("sendChatAction"),
                json={"chat_id": cid, "action": "typing"},
            )
        except Exception:
            logger.debug("Failed to send Telegram typing indicator")

    async def remove_typing_indicator(self, chat_id: str, indicator_id: str | None = None) -> None:
        """No-op: Telegram typing expires automatically."""

    def format_text(self, text: str) -> str:
        from app.formatting.telegram_md import markdown_to_telegram_html

        return markdown_to_telegram_html(text)

    def platform_name(self) -> str:
        return "telegram"

    async def set_webhook(self, url: str, secret: str | None = None) -> None:
        """Register the webhook URL and optional secret with the Telegram Bot API."""
        payload: dict = {"url": url}
        if secret:
            payload["secret_token"] = secret
        try:
            resp = await self._http.post(
                self._url("setWebhook"),
                json=payload,
            )
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram webhook registered: %s", url)
            else:
                logger.warning("Telegram setWebhook error: %s", data)
        except Exception:
            logger.exception("Failed to register Telegram webhook")
