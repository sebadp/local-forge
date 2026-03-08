"""Tests for app.telegram.parser — Telegram Update → IncomingMessage."""

from app.platforms.models import Platform
from app.telegram.parser import extract_telegram_messages


def _make_text_update(
    user_id: int = 12345678,
    message_id: int = 100,
    text: str = "Hello!",
    reply_to_id: int | None = None,
) -> dict:
    msg: dict = {
        "message_id": message_id,
        "from": {"id": user_id, "first_name": "Test"},
        "date": 1700000000,
        "text": text,
    }
    if reply_to_id is not None:
        msg["reply_to_message"] = {"message_id": reply_to_id, "from": {"id": user_id}}
    return {"update_id": 999, "message": msg}


def _make_voice_update(user_id: int = 12345678, file_id: str = "voice_file_abc") -> dict:
    return {
        "update_id": 1000,
        "message": {
            "message_id": 101,
            "from": {"id": user_id, "first_name": "Test"},
            "date": 1700000001,
            "voice": {"file_id": file_id, "duration": 3, "mime_type": "audio/ogg"},
        },
    }


def _make_photo_update(user_id: int = 12345678, caption: str = "Look at this") -> dict:
    return {
        "update_id": 1001,
        "message": {
            "message_id": 102,
            "from": {"id": user_id, "first_name": "Test"},
            "date": 1700000002,
            "photo": [
                {"file_id": "small_id", "width": 100, "height": 100, "file_size": 1024},
                {"file_id": "large_id", "width": 800, "height": 600, "file_size": 102400},
            ],
            "caption": caption,
        },
    }


def test_extract_text_message():
    update = _make_text_update(text="Hola!")
    msgs = extract_telegram_messages(update)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.platform == Platform.TELEGRAM
    assert m.user_id == "tg_12345678"
    assert m.text == "Hola!"
    assert m.type == "text"
    assert m.media_id is None
    assert m.reply_to_message_id is None


def test_extract_text_with_reply():
    update = _make_text_update(text="Thanks", reply_to_id=50)
    msgs = extract_telegram_messages(update)
    assert len(msgs) == 1
    assert msgs[0].reply_to_message_id == "tg_12345678_50"


def test_extract_voice_message():
    update = _make_voice_update(file_id="abc123")
    msgs = extract_telegram_messages(update)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.type == "audio"
    assert m.media_id == "abc123"
    assert m.text == ""


def test_extract_photo_message():
    update = _make_photo_update(caption="My cat")
    msgs = extract_telegram_messages(update)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.type == "image"
    assert m.media_id == "large_id"  # Last (largest) photo
    assert m.text == "My cat"


def test_extract_photo_no_caption():
    update = _make_photo_update(caption="")
    msgs = extract_telegram_messages(update)
    assert msgs[0].text == ""


def test_extract_edited_message():
    update = {
        "update_id": 1002,
        "edited_message": {
            "message_id": 200,
            "from": {"id": 999, "first_name": "Ed"},
            "date": 1700000010,
            "text": "Edited text",
        },
    }
    msgs = extract_telegram_messages(update)
    assert len(msgs) == 1
    assert msgs[0].user_id == "tg_999"
    assert msgs[0].text == "Edited text"


def test_extract_unsupported_type_returns_empty():
    """Stickers, videos and other unsupported types return empty list."""
    update = {
        "update_id": 1003,
        "message": {
            "message_id": 300,
            "from": {"id": 111, "first_name": "X"},
            "date": 1700000020,
            "sticker": {"file_id": "sticker_id"},
        },
    }
    assert extract_telegram_messages(update) == []


def test_extract_no_message_returns_empty():
    assert extract_telegram_messages({"update_id": 1}) == []
    assert extract_telegram_messages({}) == []


def test_message_id_is_namespaced():
    update = _make_text_update(message_id=42)
    msgs = extract_telegram_messages(update)
    assert msgs[0].message_id == "tg_12345678_42"


def test_timestamp_is_string():
    update = _make_text_update()
    msgs = extract_telegram_messages(update)
    assert msgs[0].timestamp == "1700000000"
