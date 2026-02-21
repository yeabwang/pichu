from functools import lru_cache
from typing import Callable

import tiktoken


@lru_cache(maxsize=32)
def get_tokenizer(model: str = "gpt-3.5-turbo") -> Callable[[str], list[int]]:
    try:
        encoding = tiktoken.encoding_for_model(model)
        return encoding.encode
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
        return encoding.encode


def count_tokens(text: str, model: str = "gpt-3.5-turbo") -> int:
    tokenizer = get_tokenizer(model)
    return len(tokenizer(text))


def estimate_token_count(text: str) -> int:
    return max(1, len(text) // 4)


def truncate_text_to_token_limit(
    text: str,
    max_tokens: int,
    suffix: str = "\n...[truncated]",
    model: str = "gpt-3.5-turbo",
    preserve_lines: bool = True,
) -> str:
    # Token count is always <= character count, so short strings are always under limit.
    if len(text) <= max_tokens:
        return text

    current_token_count = count_tokens(text, model=model)
    if current_token_count <= max_tokens:
        return text

    suffix_token_count = count_tokens(suffix, model=model)
    target_token_count = max_tokens - suffix_token_count

    if target_token_count <= 0:
        return suffix.strip()
    if preserve_lines:
        return _truncate_by_lines(text, target_token_count, suffix, model)
    else:
        return _truncate_by_chars(text, target_token_count, suffix, model)


def _truncate_by_lines(
    text: str,
    target_token_count: int,
    suffix: str,
    model: str,
) -> str:
    lines = text.splitlines(keepends=True) or []
    truncated_text = ""
    current_token_count = 0

    for line in lines:
        line_token_count = count_tokens(line, model=model)
        if current_token_count + line_token_count > target_token_count:
            break
        truncated_text += line
        current_token_count += line_token_count

    if not truncated_text:
        # If no lines could be added, fall back to character-based truncation
        return _truncate_by_chars(text, target_token_count, suffix, model)

    return truncated_text.rstrip() + suffix


def _truncate_by_chars(text: str, target_tokens: int, suffix: str, model: str) -> str:
    tokenizer = get_tokenizer(model)
    low, high = 0, len(text)
    best = ""

    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid]
        tokens = tokenizer(candidate)
        if len(tokens) <= target_tokens:
            best = candidate  # valid, try longer
            low = mid + 1
        else:
            high = mid - 1

    return best + suffix
