"""Regression tests for centralized logging and audit behavior."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from utils.runtime_logging import (
    audit_event,
    configure_component_file_logger,
    configure_runtime_logging,
)


def _reset_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture(autouse=True)
def _cleanup_logging_state():
    _reset_logger(logging.getLogger())
    _reset_logger(logging.getLogger("hooks"))
    _reset_logger(logging.getLogger("pichu.audit"))
    yield
    _reset_logger(logging.getLogger())
    _reset_logger(logging.getLogger("hooks"))
    _reset_logger(logging.getLogger("pichu.audit"))


def test_runtime_logging_redacts_secrets_in_file_output(tmp_path: Path):
    cfg = Config(cwd=tmp_path)
    cfg.logging.console.enabled = False
    cfg.logging.file.enabled = True
    cfg.logging.file.path = "logs/app/runtime.log"
    cfg.logging.file.json = False
    cfg.logging.audit.enabled = False

    configure_runtime_logging(cfg)
    logger = logging.getLogger("tests.runtime")
    logger.info("authorization: bearer very-secret-token")
    logger.info("api_key=super-secret-key")

    log_path = tmp_path / "logs" / "app" / "runtime.log"
    text = log_path.read_text(encoding="utf-8")
    assert "[REDACTED]" in text
    assert "very-secret-token" not in text
    assert "super-secret-key" not in text


def test_audit_event_writes_structured_security_log(tmp_path: Path):
    cfg = Config(cwd=tmp_path)
    cfg.logging.console.enabled = False
    cfg.logging.file.enabled = False
    cfg.logging.audit.enabled = True
    cfg.logging.audit.path = "logs/security/audit.log"
    cfg.logging.audit.json = True

    configure_runtime_logging(cfg)
    audit_event(
        "sandbox.blocked",
        "Filesystem sandbox blocked operation",
        operation="write",
        path="../.env",
    )

    audit_path = tmp_path / "logs" / "security" / "audit.log"
    payload = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert payload["extra"]["event"] == "sandbox.blocked"
    assert payload["extra"]["details"]["path"] == "../.env"


def test_component_file_logger_setup_is_idempotent(tmp_path: Path):
    hooks_log_path = tmp_path / "logs" / "hooks" / "hooks.log"
    configure_component_file_logger(logger_name="hooks", path=hooks_log_path, level="DEBUG")
    configure_component_file_logger(logger_name="hooks", path=hooks_log_path, level="DEBUG")

    hooks_logger = logging.getLogger("hooks")
    handlers = [
        handler
        for handler in hooks_logger.handlers
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).resolve() == hooks_log_path.resolve()
    ]
    assert len(handlers) == 1

    logging.getLogger("hooks.engine").debug("hook-log-message")
    handlers[0].flush()
    assert "hook-log-message" in hooks_log_path.read_text(encoding="utf-8")
