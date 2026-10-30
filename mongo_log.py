import atexit
import logging
import queue
import threading
import time
import traceback
from datetime import datetime, timezone

from db import errors_col

_DEDUPE_SECONDS = 60


class MongoErrorHandler(logging.Handler):
    """Copies WARNING+ log lines into Mongo so the web panel can show them.

    Writes happen in a background thread (a slow/down Mongo can no longer freeze the
    bot's event loop) and identical messages are collapsed to one row per minute, so a
    repeating error cannot flood the panel."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self._q: queue.Queue = queue.Queue(maxsize=1000)
        self._seen: dict = {}
        self._t = threading.Thread(target=self._worker, name="mongo-log", daemon=True)
        self._t.start()
        atexit.register(self.close)

    def emit(self, record):
        if record.name.startswith(("pymongo", "aiogram.event")):
            return
        try:
            msg = record.getMessage()[:1000]
            key = (record.levelname, msg[:200])
            now = time.monotonic()
            if now - self._seen.get(key, -1e9) < _DEDUPE_SECONDS:
                return
            self._seen[key] = now
            if len(self._seen) > 500:
                self._seen = {k: v for k, v in self._seen.items() if now - v < _DEDUPE_SECONDS}
            doc = {"at": datetime.now(timezone.utc), "level": record.levelname, "msg": msg}
            if record.exc_info:
                doc["trace"] = "".join(traceback.format_exception(*record.exc_info))[-2500:]
            self._q.put_nowait(doc)
        except Exception:
            pass

    def _worker(self):
        while True:
            doc = self._q.get()
            if doc is None:
                return
            try:
                errors_col.insert_one(doc)
            except Exception:
                pass

    def close(self):
        try:
            if self._t.is_alive():
                self._q.put_nowait(None)
                self._t.join(timeout=5)     # let the last (critical) line reach the panel
        except Exception:
            pass
        super().close()
