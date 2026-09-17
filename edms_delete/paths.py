import os
from pathlib import Path


def get_source_id(key: str, file_type: str) -> str:
    ft = file_type.lower()
    ext = f"{ft}.pdf" if ft in ("dgn", "dwg") else ft
    return f"{key}.{ext}"


def get_files_in_directory(directory_path: str) -> set[str]:
    path = Path(directory_path)
    if not path.is_dir():
        return set()
    return {str(entry.path) for entry in os.scandir(directory_path) if entry.is_file()}
