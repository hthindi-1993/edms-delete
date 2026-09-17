"""Configure console and file logging from extraction pipeline YAML."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


@dataclass(frozen=True)
class FileLogConfig:
    level: str
    path: str
    per_run: bool
    retention_days: int

    @property
    def enabled(self) -> bool:
        return bool(self.path.strip())


@dataclass(frozen=True)
class LoggerConfig:
    console_level: str
    file: FileLogConfig | None

    @classmethod
    def from_pipeline_yaml(cls, app_config: dict[str, Any]) -> "LoggerConfig":
        raw = app_config.get("logger") or {}
        console = raw.get("console") or {}
        file_raw = raw.get("file")

        file_config: FileLogConfig | None = None
        if isinstance(file_raw, dict) and file_raw.get("path"):
            retention = file_raw.get("retention", 0)
            file_config = FileLogConfig(
                level=str(file_raw.get("level", "DEBUG")).upper(),
                path=str(file_raw["path"]).strip(),
                per_run=bool(file_raw.get("per_run", False)),
                retention_days=int(retention) if retention is not None else 0,
            )

        return cls(
            console_level=str(console.get("level", "INFO")).upper(),
            file=file_config,
        )


def _resolve_log_path(path_str: str, run_id: str | None) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = Path.cwd() / path

    if run_id and "{run_id}" in path_str:
        path = Path(path_str.replace("{run_id}", run_id))
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    if run_id:
        stem = path.stem
        suffix = path.suffix or ".log"
        path = path.with_name(f"{stem}_{run_id}{suffix}")

    return path


def _cleanup_old_logs(log_dir: Path, name_prefix: str, retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = time.time() - (retention_days * 86400)
    for entry in log_dir.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.startswith(name_prefix):
            continue
        if entry.stat().st_mtime < cutoff:
            entry.unlink(missing_ok=True)


def configure_logging(logger_config: LoggerConfig, run_id: str | None = None) -> list[logging.Handler]:
    """
    Apply console and optional file logging.

    When file.per_run is false, the file handler is created here (includes startup logs).
    When file.per_run is true, call again with run_id at the start of the delete run.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    handlers: list[logging.Handler] = []

    console = logging.StreamHandler()
    console.setLevel(_LEVEL_MAP.get(logger_config.console_level, logging.INFO))
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)
    handlers.append(console)

    file_cfg = logger_config.file
    if file_cfg and file_cfg.enabled and (run_id is not None or not file_cfg.per_run):
        log_path = _resolve_log_path(file_cfg.path, run_id if file_cfg.per_run else None)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setLevel(_LEVEL_MAP.get(file_cfg.level, logging.DEBUG))
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
        handlers.append(file_handler)

        if file_cfg.per_run and file_cfg.retention_days > 0:
            prefix = Path(file_cfg.path).stem + "_"
            _cleanup_old_logs(log_path.parent, prefix, file_cfg.retention_days)

        logging.getLogger(__name__).info("Writing run log to %s", log_path)

    return handlers
