"""
LLM client using OpenAI-compatible API.

Supports any OpenAI-compatible endpoint (OpenAI, Azure, local models via Ollama/vLLM, etc.)
Configure via environment variables:
  OPENAI_API_KEY   - API key (required)
  OPENAI_BASE_URL  - API base URL (default: https://api.openai.com/v1)
  OPENAI_MODEL     - Model name (default: gpt-4o)
"""
import json
import os
from typing import Any, Optional

from openai import OpenAI

from ..trajectory.schemas import LLMResponse

DEFAULT_MODEL = "gpt-4o"


def _convert_tool_definitions(anthropic_tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool format to OpenAI function-calling format."""
    openai_tools = []
    for t in anthropic_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            }
        })
    return openai_tools


def _convert_messages(messages: list[dict]) -> list[dict]:
    """
    Convert messages that may contain Anthropic-style content blocks
    into OpenAI-compatible format.
    """
    converted = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "assistant":
            if isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "thinking":
                        pass  # drop thinking blocks
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"], ensure_ascii=False),
                            }
                        })

                out: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    out["content"] = "\n".join(text_parts)
                if tool_calls:
                    out["tool_calls"] = tool_calls
                converted.append(out)
            else:
                converted.append({"role": "assistant", "content": content or ""})

        elif role == "user":
            if isinstance(content, list):
                tool_results = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ]
                if tool_results:
                    for tr in tool_results:
                        inner = tr.get("content", [])
                        if isinstance(inner, list):
                            result_text = "\n".join(
                                c.get("text", "") for c in inner
                                if isinstance(c, dict)
                            )
                        else:
                            result_text = str(inner)
                        converted.append({
                            "role": "tool",
                            "tool_call_id": tr["tool_use_id"],
                            "content": result_text,
                        })
                else:
                    text = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    converted.append({"role": "user", "content": text})
            else:
                converted.append({"role": "user", "content": content})

        else:
            converted.append(msg)

    return converted


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        enable_thinking: bool = False,
        **kwargs,
    ):
        resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        resolved_base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or None  # None = use OpenAI default
        )

        if not resolved_api_key:
            raise ValueError(
                "No API key found. Set OPENAI_API_KEY environment variable.\n"
                "For custom endpoints (Azure, local models, etc.), also set OPENAI_BASE_URL."
            )

        self.client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens

    def complete(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        openai_messages = _convert_messages(all_messages)

        payload_size = len(json.dumps(openai_messages, ensure_ascii=False))
        print(f"  [LLM] payload={payload_size} chars (~{payload_size//4} tokens), msgs={len(openai_messages)}", flush=True)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = _convert_tool_definitions(tools)
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        msg = choice.message
        finish_reason = choice.finish_reason  # "stop" | "tool_calls"

        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
        }

        content_blocks = []

        if msg.content:
            content_blocks.append({"type": "text", "text": msg.content})

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {"raw": tc.function.arguments}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"

        return LLMResponse(
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=self.model,
        )
