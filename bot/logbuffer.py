"""Keeps the most recent log records in memory for the web interface."""

from __future__ import annotations
import itertools
import logging
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

MAX_RECORDS = 3000


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = MAX_RECORDS) -> None:
        super().__init__(level=logging.INFO)
        self._records: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._ids = itertools.count(1)
        self._records_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            # Each server's threads are named "server" or "server/part".
            thread = record.threadName or ""
            server = thread.split("/")[0] if thread not in ("MainThread", "") else ""
            entry = {
                "id": next(self._ids),
                "time": record.created,
                "level": record.levelname,
                "server": server,
                "thread": thread,
                "message": message,
            }
            with self._records_lock:
                self._records.append(entry)
        except Exception:
            self.handleError(record)

    def records(
        self,
        after: int = 0,
        server: Optional[str] = None,
        min_level: str = "INFO",
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        threshold = logging.getLevelName(min_level)
        if not isinstance(threshold, int):
            threshold = logging.INFO
        with self._records_lock:
            found = [
                r
                for r in self._records
                if r["id"] > after
                and (server is None or r["server"] == server)
                and logging.getLevelName(r["level"]) >= threshold
            ]
        return found[-limit:]


buffer = LogBuffer()


def install() -> None:
    root = logging.getLogger()
    if buffer not in root.handlers:
        root.addHandler(buffer)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
