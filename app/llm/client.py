from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.models import ChatMessage

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    total_duration_ms: float | None = None


class OllamaClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        base_url: str,
        model: str,
    ):
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._model = model

    def _build_message_dicts(self, messages: list[ChatMessage]) -> list[dict]:
        msg_dicts = []
        for m in messages:
            d: dict = {"role": m.role, "content": m.content}
            if m.images:
                d["images"] = m.images
            if m.tool_calls:
                d["tool_calls"] = m.tool_calls
            msg_dicts.append(d)
        return msg_dicts

    async def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        model: str | None = None,
        think: bool | None = None,
    ) -> ChatResponse:
        url = f"{self._base_url}/api/chat"
        use_model = model or self._model

        payload: dict = {
            "model": use_model,
            "messages": self._build_message_dicts(messages),
            "stream": False,
        }

        if tools:
            # think: True is incompatible with tools in qwen3
            payload["tools"] = tools
        elif think is False:
            # Explicit False: skip thinking (e.g. fast classification calls)
            # Must send explicitly — qwen3 defaults to thinking if omitted
            payload["think"] = False
        elif model is None:
            # Only enable thinking for default chat model without tools
            payload["think"] = True

        resp = await self._http.post(url, json=payload)
        if resp.status_code == 404:
            logger.error(
                "Ollama model '%s' not found — download it with: "
                "docker compose exec ollama ollama pull %s",
                use_model,
                use_model,
            )
        resp.raise_for_status()
        data = resp.json()
        msg = data["message"]
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        # Extract generation metrics from Ollama response
        input_tokens: int | None = data.get("prompt_eval_count")
        output_tokens: int | None = data.get("eval_count")
        total_dur = data.get("total_duration")
        total_duration_ms: float | None = total_dur / 1_000_000 if total_dur is not None else None

        if content:
            logger.debug("LLM raw response: %s", content[:500])
            # Strip deepseek/qwen reasoning blocks: <think>...</think>
            content = re.sub(r"<think>.*?</think>\n*", "", content, flags=re.DOTALL)
            # Edge-cases if the LLM gets truncated exactly after opening or closing tags
            content = content.split("</think>")[-1]
            content = content.split("<think>")[0].strip()

        logger.debug("LLM processed response: %s", content[:500] if content else "(tool_calls)")
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=use_model,
            total_duration_ms=total_duration_ms,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        think: bool | None = None,
    ) -> str:
        response = await self.chat_with_tools(messages, tools=None, model=model, think=think)
        return response.content

    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        """Generate embeddings for a batch of texts via POST /api/embed."""
        url = f"{self._base_url}/api/embed"
        use_model = model or self._model
        payload = {"model": use_model, "input": texts}
        resp = await self._http.post(url, json=payload)
        if resp.status_code == 404:
            logger.error(
                "Ollama embedding model '%s' not found — download it with: "
                "docker compose exec ollama ollama pull %s",
                use_model,
                use_model,
            )
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"]

    async def is_available(self) -> bool:
        try:
            resp = await self._http.get(
                f"{self._base_url}/api/tags",
                timeout=5.0,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
