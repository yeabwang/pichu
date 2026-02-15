from pathlib import Path


def resolve_path(base: str | Path, path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path.resolve()
    return (Path(base).resolve() / path).resolve()


def display_path_relative_to_cwd(path: str | Path, cwd: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(cwd).resolve()))
    except ValueError:
        return str(Path(path).resolve())


def is_binary_file(file_path: str | Path, sample_size: int = 8192) -> bool:
    try:
        with open(file_path, "rb") as f:
            return b"\x00" in f.read(sample_size)
    except OSError:
        return False


def ensure_parent_directory(file_path: str | Path) -> Path:
    parent_dir = Path(file_path).parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    return parent_dir
