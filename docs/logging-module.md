# Logging Module

> `utils/runtime_logging.py` — centralized runtime and audit logging.

## Overview

The logging module bootstraps consistent logging across all components with secret redaction, rotating file handlers, and a dedicated security audit stream.

## Public API

```python
from utils.runtime_logging import (
    configure_runtime_logging,
    configure_component_file_logger,
    audit_event,
)
```

## Functions

| Function | Description |
|----------|-------------|
| `configure_runtime_logging(config)` | Bootstraps root logging from config (level, handlers, per-logger overrides) |
| `configure_component_file_logger(name, path, ...)` | Adds a rotating file handler for a specific namespace (e.g., hooks) |
| `audit_event(event, message, **details)` | Writes structured security events to the audit sink |

## Usage Example

```python
import logging
from utils.runtime_logging import audit_event

logger = logging.getLogger(__name__)
logger.info("Processing request")

# Security-significant events go to the audit log
audit_event("approval_denied", "Shell command blocked", command="rm -rf /")
```

## Safety Features

- **Secret redaction** — filters mask common token, password, and API-key patterns in log messages and structured metadata.
- **Audit stream** — captures safety-significant events (approval denials, sandbox blocks, hook/tool denials, blocked shell commands).
- **Rotating handlers** — prevent unbounded log growth.

## Configuration

Logging settings live in `Config.logging`:

```toml
[logging]
level = "INFO"

[logging.console]
enabled = false
level = "DEBUG"
use_rich = true

[logging.file]
enabled = true
level = "INFO"
path = "logs/app/pichu.log"
max_bytes = 5242880
backup_count = 5

[logging.audit]
enabled = true
level = "INFO"
path = "logs/security/audit.log"
json = true

[logging.redaction]
enabled = true
```

## Extension Points

- Use `logging.getLogger(__name__)` in modules as usual — root bootstrap is centralized.
- For security/permission/sandbox actions, call `audit_event(...)` with stable event names.
- For subsystem-specific log files, use `configure_component_file_logger(...)` instead of manual `FileHandler` setup.

## Related

- [Config Module](config-module.md) — `LoggingConfig` schema
- [Safety Module](safety-module.md) — audit events for approval/sandbox actions
