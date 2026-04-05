"""Tests for WhatsApp reaction → trace score pipeline (Plan 61 Phase 4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import WhatsAppReaction


@pytest.fixture
def reaction_thumbs_up():
    return WhatsAppReaction(
        from_number="5491112345678",
        reacted_message_id="wamid.abc123",
        emoji="👍",
    )


@pytest.fixture
def reaction_thumbs_down():
    return WhatsAppReaction(
        from_number="5491112345678",
        reacted_message_id="wamid.abc123",
        emoji="👎",
    )


@pytest.fixture
def mock_repository():
    repo = AsyncMock()
    repo.get_trace_id_by_wa_message_id = AsyncMock(return_value="trace-001")
    repo.save_trace_score = AsyncMock()
    repo.get_trace_io_by_id = AsyncMock(return_value=("hola", "chau"))
    repo.get_trace_scores = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_wa_client():
    client = AsyncMock()
    client.send_message = AsyncMock()
    return client


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.eval_auto_curate = False
    return s


async def test_thumbs_up_scores_1(reaction_thumbs_up, mock_repository, mock_settings):
    from app.webhook.router import _handle_reaction

    await _handle_reaction(reaction_thumbs_up, mock_repository, settings=mock_settings)

    mock_repository.save_trace_score.assert_called_once_with(
        trace_id="trace-001",
        name="user_reaction",
        value=1.0,
        source="user",
        comment="👍",
    )


async def test_thumbs_down_scores_0(reaction_thumbs_down, mock_repository, mock_wa_client, mock_settings):
    from app.webhook.router import _handle_reaction

    await _handle_reaction(reaction_thumbs_down, mock_repository, mock_wa_client, mock_settings)

    mock_repository.save_trace_score.assert_any_call(
        trace_id="trace-001",
        name="user_reaction",
        value=0.0,
        source="user",
        comment="👎",
    )


async def test_unknown_message_ignored(mock_repository, mock_settings):
    from app.webhook.router import _handle_reaction

    mock_repository.get_trace_id_by_wa_message_id = AsyncMock(return_value=None)
    reaction = WhatsAppReaction(
        from_number="5491112345678",
        reacted_message_id="wamid.unknown",
        emoji="👍",
    )
    await _handle_reaction(reaction, mock_repository, settings=mock_settings)
    mock_repository.save_trace_score.assert_not_called()


async def test_non_reaction_object_ignored(mock_repository, mock_settings):
    from app.webhook.router import _handle_reaction

    await _handle_reaction("not a reaction", mock_repository, settings=mock_settings)
    mock_repository.save_trace_score.assert_not_called()


async def test_unknown_emoji_defaults_to_05(mock_repository, mock_settings):
    from app.webhook.router import _handle_reaction

    reaction = WhatsAppReaction(
        from_number="5491112345678",
        reacted_message_id="wamid.abc123",
        emoji="🔥",
    )
    await _handle_reaction(reaction, mock_repository, settings=mock_settings)
    mock_repository.save_trace_score.assert_called_once_with(
        trace_id="trace-001",
        name="user_reaction",
        value=0.5,
        source="user",
        comment="🔥",
    )


async def test_negative_reaction_sends_correction_prompt(
    reaction_thumbs_down, mock_repository, mock_wa_client, mock_settings
):
    from app.webhook.router import _handle_reaction

    await _handle_reaction(reaction_thumbs_down, mock_repository, mock_wa_client, mock_settings)

    # Should have saved correction_prompted score
    calls = mock_repository.save_trace_score.call_args_list
    correction_calls = [c for c in calls if c.kwargs.get("name") == "correction_prompted"]
    assert len(correction_calls) == 1

    # Should have sent a message asking for correction
    mock_wa_client.send_message.assert_called_once()
    msg_text = mock_wa_client.send_message.call_args[0][1]
    assert "correcta" in msg_text.lower() or "debería" in msg_text.lower()


async def test_negative_reaction_no_double_prompt(
    reaction_thumbs_down, mock_repository, mock_wa_client, mock_settings
):
    """If already prompted, don't send again."""
    from app.webhook.router import _handle_reaction

    mock_repository.get_trace_scores = AsyncMock(
        return_value=[{"name": "correction_prompted", "value": 1.0}]
    )
    await _handle_reaction(reaction_thumbs_down, mock_repository, mock_wa_client, mock_settings)
    mock_wa_client.send_message.assert_not_called()


def test_extract_reactions_from_payload():
    from app.webhook.parser import extract_reactions

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5491112345678",
                                    "type": "reaction",
                                    "reaction": {
                                        "message_id": "wamid.target",
                                        "emoji": "👍",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    reactions = extract_reactions(payload)
    assert len(reactions) == 1
    assert reactions[0].emoji == "👍"
    assert reactions[0].reacted_message_id == "wamid.target"


def test_extract_reactions_ignores_non_reactions():
    from app.webhook.parser import extract_reactions

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "123", "type": "text", "text": {"body": "hi"}}
                            ]
                        }
                    }
                ]
            }
        ]
    }
    assert extract_reactions(payload) == []
