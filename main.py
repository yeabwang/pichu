# ruff: noqa: E402

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_ROOT = _PROJECT_ROOT / "src"
if _SRC_ROOT.exists():
    src_path = str(_SRC_ROOT)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

import pichu_main as _impl

CLI = _impl.CLI
main = _impl.main


def __getattr__(name: str):
    return getattr(_impl, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_impl)))


if __name__ == "__main__":
    main()
