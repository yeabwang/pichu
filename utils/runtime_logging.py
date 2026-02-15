"""Centralized runtime logging with redaction, rotation, and audit events."""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

try:
    from rich.logging import RichHandler
except Exception:  # pragma: no cover - optional dependency fallback
    RichHandler = None  # type: ignore[assignment, misc]

if TYPE_CHECKING:
    from config.config import Config, LoggingRedactionConfig


_AUDIT_LOGGER_NAME = "pichu.audit"
_STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys())
_state_lock = Lock()
_runtime_redaction_filter: "SecretRedactionFilter | None" = None
_active_logging_config: Any | None = None
_active_cwd: Path | None = None


def _coerce_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _resolve_path(cwd: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (cwd / path).resolve()


def _close_and_remove_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


class SecretRedactionFilter(logging.Filter):
    """Sanitize log messages/fields before they reach handlers."""

    def __init__(
        self,
        *,
        enabled: bool,
        replacement: str,
        patterns: list[str],
        sensitive_key_fragments: list[str],
    ) -> None:
        super().__init__()
        self._enabled = enabled
        self._replacement = replacement
        self._patterns = [re.compile(pattern) for pattern in patterns]
        self._sensitive_key_fragments = {fragment.lower() for fragment in sensitive_key_fragments}

    def _redact_text(self, text: str) -> str:
        redacted = text
        for pattern in self._patterns:
            redacted = pattern.sub(self._replace_match, redacted)
        return redacted

    def _replace_match(self, match: re.Match[str]) -> str:
        if match.lastindex and match.lastindex >= 2:
            whole = match.group(0)
            secret = match.group(match.lastindex)
            if secret:
                return whole.replace(secret, self._replacement)
        return self._replacement

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.lower()
        return any(fragment in normalized for fragment in self._sensitive_key_fragments)

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            sanitized: dict[Any, Any] = {}
            for key, nested_value in value.items():
                if isinstance(key, str) and self._is_sensitive_key(key):
                    sanitized[key] = self._replacement
                else:
                    sanitized[key] = self._sanitize(nested_value)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize(item) for item in value)
        if isinstance(value, set):
            return {self._sanitize(item) for item in value}
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._enabled:
            return True
        try:
            record.msg = self._sanitize(record.msg)
            if record.args:
                record.args = self._sanitize(record.args)

            for key, value in list(record.__dict__.items()):
                if key in _STANDARD_LOG_RECORD_FIELDS:
                    continue
                if self._is_sensitive_key(key):
                    record.__dict__[key] = self._replacement
                else:
                    record.__dict__[key] = self._sanitize(value)
        except Exception:
            # Logging must remain best-effort and never break runtime behavior.
            return True
        return True


class JsonLogFormatter(logging.Formatter):
    """Structured JSON formatter for machine-readable logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS:
                continue
            extra[key] = value
        if extra:
            payload["extra"] = extra

        return json.dumps(payload, default=str, ensure_ascii=True)


def _build_redaction_filter(
    redaction_config: "LoggingRedactionConfig",
) -> SecretRedactionFilter:
    return SecretRedactionFilter(
        enabled=redaction_config.enabled,
        replacement=redaction_config.replacement,
        patterns=redaction_config.patterns,
        sensitive_key_fragments=redaction_config.sensitive_key_fragments,
    )


def _create_rotating_handler(
    *,
    path: Path,
    level: str | int,
    max_bytes: int,
    backup_count: int,
) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(_coerce_level(level))
    return handler


def configure_runtime_logging(config: "Config") -> None:
    """Configure root/runtime logging using the loaded application config."""
    global _runtime_redaction_filter, _active_logging_config, _active_cwd

    with _state_lock:
        _active_logging_config = config.logging
        _active_cwd = Path(config.cwd).resolve()
        _runtime_redaction_filter = _build_redaction_filter(config.logging.redaction)

        root_logger = logging.getLogger()
        _close_and_remove_handlers(root_logger)
        root_logger.setLevel(_coerce_level(config.logging.level))

        console_enabled = config.logging.console.enabled or config.debug
        if console_enabled:
            console_handler: logging.Handler
            if config.logging.console.use_rich and RichHandler is not None:
                console_handler = RichHandler(
                    rich_tracebacks=True,
                    show_path=config.logging.console.show_path,
                    markup=config.logging.console.markup,
                )
                console_handler.setFormatter(logging.Formatter(config.logging.console.format))
            else:
                console_handler = logging.StreamHandler()
                console_handler.setFormatter(logging.Formatter(config.logging.console.format))
            console_handler.setLevel(_coerce_level(config.logging.console.level))
            console_handler.addFilter(_runtime_redaction_filter)
            root_logger.addHandler(console_handler)

        if config.logging.file.enabled:
            runtime_log_path = _resolve_path(_active_cwd, config.logging.file.path)
            file_handler = _create_rotating_handler(
                path=runtime_log_path,
                level=config.logging.file.level,
                max_bytes=config.logging.file.max_bytes,
                backup_count=config.logging.file.backup_count,
            )
            if config.logging.file.json:
                file_handler.setFormatter(JsonLogFormatter())
            else:
                file_handler.setFormatter(logging.Formatter(config.logging.file.format))
            file_handler.addFilter(_runtime_redaction_filter)
            root_logger.addHandler(file_handler)

        for logger_name, level in config.logging.per_logger_levels.items():
            logging.getLogger(logger_name).setLevel(_coerce_level(level))

        audit_logger = logging.getLogger(_AUDIT_LOGGER_NAME)
        _close_and_remove_handlers(audit_logger)
        audit_logger.propagate = False


def configure_component_file_logger(
    *,
    logger_name: str,
    path: str | Path,
    level: str | int = "DEBUG",
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
    format_string: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
) -> None:
    """Attach an idempotent rotating file handler to a named logger."""
    logger = logging.getLogger(logger_name)
    resolved_path = Path(path).resolve()

    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            base_file = getattr(handler, "baseFilename", None)
            if base_file and Path(base_file).resolve() == resolved_path:
                return

    handler = _create_rotating_handler(
        path=resolved_path,
        level=level,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    handler.setFormatter(logging.Formatter(format_string))

    if _runtime_redaction_filter:
        handler.addFilter(_runtime_redaction_filter)

    logger.addHandler(handler)
    target_level = _coerce_level(level)
    current_level = logger.level if logger.level != logging.NOTSET else logging.CRITICAL
    logger.setLevel(min(current_level, target_level))


def _ensure_audit_logger() -> logging.Logger | None:
    if _active_logging_config is None or _active_cwd is None:
        return None
    if not _active_logging_config.audit.enabled:
        return None

    logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    audit_log_path = _resolve_path(_active_cwd, _active_logging_config.audit.path)

    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            base_file = getattr(handler, "baseFilename", None)
            if base_file and Path(base_file).resolve() == audit_log_path:
                return logger

    _close_and_remove_handlers(logger)
    logger.propagate = False
    logger.setLevel(_coerce_level(_active_logging_config.audit.level))

    handler = _create_rotating_handler(
        path=audit_log_path,
        level=_active_logging_config.audit.level,
        max_bytes=_active_logging_config.audit.max_bytes,
        backup_count=_active_logging_config.audit.backup_count,
    )
    if _active_logging_config.audit.json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(_active_logging_config.audit.format))

    if _runtime_redaction_filter:
        handler.addFilter(_runtime_redaction_filter)

    logger.addHandler(handler)
    return logger


def audit_event(event: str, message: str, **details: Any) -> None:
    """Write a structured audit event if audit logging is configured."""
    with _state_lock:
        logger = _ensure_audit_logger()
        if logger is None:
            return
        try:
            logger.info(message, extra={"event": event, "details": details})
        except Exception:
            # Never fail caller flows for logging issues.
            return


__all__ = [
    "JsonLogFormatter",
    "SecretRedactionFilter",
    "audit_event",
    "configure_component_file_logger",
    "configure_runtime_logging",
]
