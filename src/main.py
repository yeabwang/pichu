"""Compatibility entrypoint shim.

The canonical CLI implementation lives in the repository-root ``main.py``.
This wrapper avoids keeping a duplicated runtime implementation under ``src/``.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_ROOT_MAIN_PATH = Path(__file__).resolve().parent.parent / "main.py"
_spec = spec_from_file_location("_pichu_root_main", _ROOT_MAIN_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load root main module from {_ROOT_MAIN_PATH}")

_root_main = module_from_spec(_spec)
_spec.loader.exec_module(_root_main)

BANNER = _root_main.BANNER
CLI = _root_main.CLI
_configure_logging = _root_main._configure_logging
console = _root_main.console
main = _root_main.main

__all__ = ["BANNER", "CLI", "_configure_logging", "console", "main"]


if __name__ == "__main__":
    main()
