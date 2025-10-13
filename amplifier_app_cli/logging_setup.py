"""
App-layer JSONL logging bootstrap.
Initializes a single canonical JSONL sink early in CLI startup.
"""

import json
import logging
import os
from datetime import UTC
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = os.environ.get("AMPLIFIER_LOG_PATH", "./amplifier.log.jsonl")
DEFAULT_LEVEL = os.environ.get("AMPLIFIER_LOG_LEVEL", "INFO").upper()


class JsonlHandler(logging.Handler):
    def __init__(self, path: str):
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Build a structured payload
            base = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "lvl": record.levelname,
                "schema": {"name": "amplifier.log", "ver": "1.0.0"},
                "logger": record.name,
                "event": getattr(record, "event", None),
                "message": record.getMessage(),
            }
            # Merge extras if the message is a dict
            msg = record.msg
            if isinstance(msg, dict):
                base.update(msg)
            # Attach any extra fields on the record
            for k, v in record.__dict__.items():
                if k in (
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "name",
                ):
                    continue
                base.setdefault(k, v)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(base, ensure_ascii=False) + "\n")
        except Exception:
            # As a last resort, swallow errors to avoid interfering with main flow
            pass


def init_json_logging(path: str | None = None, level: str | None = None) -> None:
    path = path or DEFAULT_PATH
    level = (level or DEFAULT_LEVEL).upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    # Remove existing handlers of the same kind to avoid duplicates
    for h in list(root.handlers):
        if isinstance(h, JsonlHandler):
            root.removeHandler(h)
    root.addHandler(JsonlHandler(path))
