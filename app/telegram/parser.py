from __future__ import annotations

from app.platforms.models import IncomingMessage, Platform

_SUPPORTED_TYPES = {"text", "voice", "audio", "photo"}


def extract_telegram_messages(payload: dict) -> list[IncomingMessage]:
    """Convert a Telegram Update payload to a list of IncomingMessage.

    Handles message and edited_message updates.
    Supported content types: text, voice, audio, photo (largest size).
    Unsupported types (stickers, video, etc.) are silently ignored.
    """
    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return []

    from_info = msg.get("from")
    if not from_info:
        return []

    user_id = f"tg_{from_info['id']}"
    message_id = f"tg_{from_info['id']}_{msg['message_id']}"
    timestamp = str(msg["date"])
    reply_to: str | None = None
    if "reply_to_message" in msg:
        reply_to = str(msg["reply_to_message"]["message_id"])

    if "text" in msg:
        return [
            IncomingMessage(
                platform=Platform.TELEGRAM,
                user_id=user_id,
                message_id=message_id,
                timestamp=timestamp,
                text=msg["text"],
                type="text",
                reply_to_message_id=reply_to,
            )
        ]

    if "voice" in msg or "audio" in msg:
        obj = msg.get("voice") or msg.get("audio", {})
        return [
            IncomingMessage(
                platform=Platform.TELEGRAM,
                user_id=user_id,
                message_id=message_id,
                timestamp=timestamp,
                text="",
                type="audio",
                media_id=obj.get("file_id"),
                reply_to_message_id=reply_to,
            )
        ]

    if "photo" in msg:
        # Use last photo (highest resolution)
        file_id = msg["photo"][-1]["file_id"]
        caption = msg.get("caption", "")
        return [
            IncomingMessage(
                platform=Platform.TELEGRAM,
                user_id=user_id,
                message_id=message_id,
                timestamp=timestamp,
                text=caption,
                type="image",
                media_id=file_id,
                reply_to_message_id=reply_to,
            )
        ]

    return []
