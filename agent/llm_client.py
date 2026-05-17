"""
LLM client using Anthropic SDK (Messages API).

Supports any Anthropic-compatible endpoint.
Configure via environment variables or .env:
  OPENAI_API_KEY   - API key (required, kept for backward compat)
  OPENAI_BASE_URL  - API base URL (default: https://api.anthropic.com)
  OPENAI_MODEL     - Model name (default: claude-sonnet-4-6)
"""
import json
import os
from typing import Any, Optional

from anthropic import Anthropic

from ..trajectory.schemas import LLMResponse

DEFAULT_MODEL = "claude-sonnet-4-6"


def _anthropic_tools_to_dicts(tools: list[dict]) -> list[dict]:
    """Ensure tools are in Anthropic format (they already are, just pass through)."""
    return tools


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 16000,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        **kwargs,
    ):
        resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        resolved_base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.anthropic.com"
        )

        if not resolved_api_key:
            raise ValueError(
                "No API key found. Set OPENAI_API_KEY environment variable.\n"
                "For custom endpoints, also set OPENAI_BASE_URL."
            )

        self.client = Anthropic(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        # Messages are already in Anthropic format (content blocks as lists)
        payload_size = len(json.dumps(messages, ensure_ascii=False))
        print(
            f"  [LLM] endpoint={self.client.base_url}/v1/messages model={self.model}",
            flush=True,
        )
        print(
            f"  [LLM] payload={payload_size} chars (~{payload_size//4} tokens), "
            f"msgs={len(messages)}",
            flush=True,
        )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _anthropic_tools_to_dicts(tools)
        if self.enable_thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }

        response = self.client.messages.create(**kwargs)

        if not hasattr(response, "content"):
            print(
                f"  [LLM] UNEXPECTED response type={type(response).__name__}: "
                f"{str(response)[:500]}",
                flush=True,
            )
            raise RuntimeError(
                f"API returned {type(response).__name__} instead of Message object"
            )

        # Build content blocks from Anthropic response (already in our internal format)
        content_blocks: list[dict] = []
        for block in response.content:
            if hasattr(block, "model_dump"):
                content_blocks.append(block.model_dump())
            elif isinstance(block, dict):
                content_blocks.append(block)
            else:
                content_blocks.append({"type": "text", "text": str(block)})

        usage = {
            "input_tokens": response.usage.input_tokens if response.usage else 0,
            "output_tokens": response.usage.output_tokens if response.usage else 0,
        }

        return LLMResponse(
            content=content_blocks,
            stop_reason=response.stop_reason or "end_turn",
            usage=usage,
            model=self.model,
        )
