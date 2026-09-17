import hashlib
from datetime import UTC, datetime


def get_current_time() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def create_run_id(timestamp: str) -> str:
    return hashlib.md5(timestamp.encode()).hexdigest()
