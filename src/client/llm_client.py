"""OpenAI-compatible async client with stream-first event normalization."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Callable

import httpx
from config import LLMConfig, RetryConfig, get_config
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from utils.text import count_tokens as count_text_tokens

from client.models import (
    StreamEvent,
    StreamEventType,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    parse_tool_call_arguments,
)

logger = logging.getLogger(__name__)
RetryCallback = Callable[[int, int, str, float], None]


def create_retry_decorator(config: RetryConfig, on_retry: RetryCallback | None = None):
    """Build retry behavior for provider request failures."""

    def _before_sleep(retry_state):
        attempt = retry_state.attempt_number
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        wait = retry_state.next_action.sleep if retry_state.next_action else 0
        error_str = str(exc) if exc else "unknown error"
        logger.warning(
            "LLM retry %s/%s: %s (waiting %.0fs)",
            attempt,
            config.max_attempts,
            error_str,
            wait,
        )
        if on_retry:
            on_retry(attempt, config.max_attempts, error_str, wait)

    return retry(
        wait=wait_exponential(
            multiplier=config.multiplier,
            min=config.min_wait,
            max=config.max_wait,
        ),
        stop=stop_after_attempt(config.max_attempts),
        retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError, httpx.ReadTimeout)),
        before_sleep=_before_sleep,
        reraise=True,
    )


class LLMClient:
    """Provider-agnostic chat completion client with normalized stream events."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._client: AsyncOpenAI | None = None
        self._config = config or get_config().llm
        self._retry_callbacks: list[RetryCallback] = []
        self._create_completion = create_retry_decorator(  # type: ignore[method-assign]
            self._config.retry,
            on_retry=self._notify_retry,
        )(self._create_completion)

    def on_retry(self, callback: RetryCallback) -> None:
        """Register retry callback: (attempt, max_attempts, error, wait_seconds)."""
        self._retry_callbacks.append(callback)

    def remove_retry_callback(self, callback: RetryCallback) -> None:
        """Remove a retry callback if it is currently registered."""
        if callback in self._retry_callbacks:
            self._retry_callbacks.remove(callback)

    def _notify_retry(self, attempt: int, max_attempts: int, error: str, wait_seconds: float) -> None:
        for cb in self._retry_callbacks:
            try:
                cb(attempt, max_attempts, error, wait_seconds)
            except Exception as exc:
                logger.debug("Retry callback failed: %s", exc)

    async def __aenter__(self) -> LLMClient:
        self.get_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def get_client(self) -> AsyncOpenAI:
        """Get or initialize the underlying OpenAI-compatible async client."""
        if self._client is None:
            errors = []
            if not self._config.api_key:
                errors.append("LLM API key not set. Set LLM_API_KEY environment variable or use /login to configure.")
            if not self._config.base_url:
                errors.append("LLM base URL not set. Set LLM_BASE_URL environment variable.")
            if not self._config.model:
                errors.append("LLM model not set. Set LLM_MODEL environment variable.")
            if errors:
                raise ValueError("\n".join(errors))
            self._client = AsyncOpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                timeout=self._config.timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.close()
            self._client = None

    def count_tokens(self, messages: list[dict[str, Any]], model: str | None = None) -> int:
        """Estimate prompt token count for OpenAI-style message payloads."""
        model = model or self._config.model
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if content:
                serialized = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                total += count_text_tokens(serialized, model)

            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                total += count_text_tokens(json.dumps(tool_calls), model)

            total += 4

        return total

    def _build_tool_schema(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert internal tool specs to OpenAI function-tool schema."""
        schemas: list[dict[str, Any]] = []
        for tool in tools:
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Tool schema requires a non-empty 'name' field.")
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "parameters",
                            {
                                "type": "object",
                                "properties": {},
                            },
                        ),
                    },
                }
            )
        return schemas

    def _build_completion_kwargs(
        self,
        messages: list[dict[str, Any]],
        stream: bool,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build a provider request payload from runtime config and inputs."""
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "stream": stream,
            "temperature": self._config.temperature,
        }
        if self._config.max_tokens is not None:
            kwargs["max_tokens"] = self._config.max_tokens
        if tools:
            kwargs["tools"] = self._build_tool_schema(tools)
            kwargs["tool_choice"] = "auto"
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    @staticmethod
    def _usage_from_response(raw_usage: Any) -> TokenUsage | None:
        """Convert provider usage payloads into `TokenUsage`."""
        if raw_usage is None:
            return None
        details = getattr(raw_usage, "prompt_tokens_details", None)
        cache_tokens = getattr(details, "cached_tokens", 0) if details else 0
        return TokenUsage(
            prompt_tokens=raw_usage.prompt_tokens,
            completion_tokens=raw_usage.completion_tokens,
            total_tokens=raw_usage.total_tokens,
            cache_tokens=cache_tokens,
        )

    async def _create_completion(
        self,
        client: AsyncOpenAI,
        kwargs: dict[str, Any],
    ) -> Any:
        """Execute the provider call (wrapped with retry in `__init__`)."""
        return await client.chat.completions.create(**kwargs)

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield normalized events for streaming or non-stream completions."""
        try:
            client = self.get_client()
            kwargs = self._build_completion_kwargs(messages, stream, tools)
            if stream:
                async for event in self._stream_response(client, kwargs):
                    yield event
            else:
                event = await self._non_stream_response(client, kwargs)
                yield event

        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            logger.error(f"API error after retries: {e}")
            yield StreamEvent.from_error(str(e))
        except httpx.ReadTimeout as e:
            logger.error(f"Read timeout during streaming: {e}")
            yield StreamEvent.from_error(
                "Request timed out while reading response. The server may be overloaded. Please try again."
            )
        except APIError as e:
            logger.error(f"API error (non-retryable): {e}")
            yield StreamEvent.from_error(str(e))

    async def _stream_response(self, client: AsyncOpenAI, kwargs: dict[str, Any]) -> AsyncGenerator[StreamEvent, None]:
        usage: TokenUsage | None = None
        finish_reason: str | None = None
        tool_calls: dict[int, dict[str, Any]] = {}

        response = await self._create_completion(client, kwargs)

        async for chunk in response:
            chunk_usage = self._usage_from_response(getattr(chunk, "usage", None))
            if chunk_usage is not None:
                usage = chunk_usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if choice.finish_reason:
                finish_reason = choice.finish_reason

            if delta.content:
                yield StreamEvent.from_text(delta.content)

            if delta.tool_calls:
                for tool_call_delta in delta.tool_calls:
                    idx = tool_call_delta.index

                    if idx not in tool_calls:
                        tool_calls[idx] = {
                            "id": tool_call_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }

                    if tool_call_delta.id:
                        tool_calls[idx]["id"] = tool_call_delta.id

                    if tool_call_delta.function:
                        if tool_call_delta.function.name:
                            tool_calls[idx]["name"] = tool_call_delta.function.name
                            if not tool_calls[idx].get("started"):
                                yield StreamEvent(
                                    type=StreamEventType.TOOL_CALL_START,
                                    tool_call_delta=ToolCallDelta(
                                        call_id=tool_calls[idx]["id"],
                                        name=tool_calls[idx]["name"],
                                    ),
                                )
                                tool_calls[idx]["started"] = True
                        if tool_call_delta.function.arguments:
                            tool_calls[idx]["arguments"] += tool_call_delta.function.arguments
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_DELTA,
                                tool_call_delta=ToolCallDelta(
                                    call_id=tool_calls[idx]["id"],
                                    name=tool_calls[idx]["name"],
                                    arguments_delta=tool_call_delta.function.arguments,
                                ),
                            )

        # Yield complete tool calls after streaming is done
        for idx in sorted(tool_calls):
            tool_call = tool_calls[idx]
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL_COMPLETE,
                tool_call=ToolCall(
                    id=tool_call["id"],
                    name=tool_call["name"],
                    arguments=parse_tool_call_arguments(tool_call["arguments"]),
                ),
            )

        yield StreamEvent.from_finish(finish_reason, usage)

    async def _non_stream_response(
        self,
        client: AsyncOpenAI,
        kwargs: dict[str, Any],
    ) -> StreamEvent:
        response = await self._create_completion(client, kwargs)
        choice = response.choices[0]
        message = choice.message

        text_delta = TextDelta(content=message.content) if message.content else None
        usage = self._usage_from_response(response.usage)

        finish_event = StreamEvent.from_finish(choice.finish_reason, usage, text_delta)
        if message.tool_calls:
            serialized_tool_calls = []
            for tool_call in message.tool_calls:
                serialized_tool_calls.append(
                    {
                        "id": tool_call.id or "",
                        "name": tool_call.function.name if tool_call.function else "",
                        "arguments": parse_tool_call_arguments(
                            tool_call.function.arguments if tool_call.function and tool_call.function.arguments else ""
                        ),
                    }
                )
            finish_event.data = {
                **(finish_event.data or {}),
                "tool_calls": serialized_tool_calls,
            }
        return finish_event
