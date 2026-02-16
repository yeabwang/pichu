"""Context compaction by summarizing long conversation histories."""

from __future__ import annotations

import logging
from typing import Any

from client.llm_client import LLMClient
from client.models import StreamEventType, TokenUsage
from config import Config
from prompts.system import get_compression_prompt
from utils.text import truncate_text_to_token_limit

from context.context_manager import ContextManager

logger = logging.getLogger(__name__)


class ChatCompactor:
    """Builds compacted summaries when prompt context exceeds budget."""

    def __init__(self, client: LLMClient, config: Config) -> None:
        self._client = client
        self._config = config
        self._truncation_tokens = max(1, config.limits.tool_output_display_tokens)

    def _truncate_text(self, content: str, suffix: str) -> str:
        return truncate_text_to_token_limit(
            content,
            max_tokens=self._truncation_tokens,
            suffix=suffix,
            model=self._config.model,
        )

    def _format_tool_call(self, tool_call: dict[str, Any]) -> str:
        function_info = tool_call.get("function", {})
        name = function_info.get("name", "unknown_function")
        arguments = str(function_info.get("arguments", ""))
        if arguments:
            arguments = self._truncate_text(
                arguments,
                suffix="\n...[function arguments truncated]",
            )
        return f"Function: {name}\nArguments: {arguments}\n"

    def _format_history_for_compaction(self, messages: list[dict[str, Any]]) -> str:
        """Format message history into a provider-friendly summarization prompt body."""
        formatted_parts = ["Here is the conversation history that needs to be continued:\n\n"]

        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "") or ""

            if role == "system":
                continue
            elif role == "tool":
                tool_id = message.get("tool_call_id", "unknown_tool")
                truncated_content = self._truncate_text(
                    content,
                    suffix="\n...[tool output truncated]",
                )
                formatted_parts.append(f"[Tool result of: {tool_id}]\n{truncated_content}\n\n")
            elif role == "assistant":
                tool_details = [self._format_tool_call(tool_call) for tool_call in message.get("tool_calls", [])]
                tool_details_str = "\n".join(tool_details)
                formatted_parts.append(f"[Assistant message]\n{content}\n{tool_details_str}\n\n")
            elif role == "user":
                content = self._truncate_text(
                    content,
                    suffix="\n...[user message truncated]",
                )
                formatted_parts.append(f"[User message]\n{content}\n\n")

        return "".join(formatted_parts)

    async def compact_context(
        self,
        context_manager: ContextManager,
        max_tokens: int,
        system_prompt: str | None = None,
    ) -> tuple[str | None, TokenUsage | None, int]:
        """Summarize context history when current token usage exceeds `max_tokens`."""
        messages = context_manager.get_messages()
        original_tokens = self._client.count_tokens(messages)

        if original_tokens <= max_tokens:
            return None, None, 0

        prompt_text = get_compression_prompt()
        if system_prompt:
            prompt_text = f"{prompt_text}\n\nAdditional focus:\n{system_prompt.strip()}"
        compression_messages = [
            {
                "role": "system",
                "content": prompt_text,
            },
            {
                "role": "user",
                "content": self._format_history_for_compaction(messages),
            },
        ]

        summary = ""
        usage: TokenUsage | None = None

        async for event in self._client.chat_completion(
            compression_messages,
            stream=False,
        ):
            if event.type == StreamEventType.MESSAGE_COMPLETE:
                usage = event.usage
                if event.text_delta:
                    summary = event.text_delta.content

        if not summary.strip():
            logger.warning("Compaction completed without a summary payload.")
            return None, usage, original_tokens

        return summary.strip(), usage, original_tokens
