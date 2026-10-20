import logging
import traceback
from datetime import datetime, timezone

from db import errors_col


class MongoErrorHandler(logging.Handler):
    """Copies WARNING+ log lines into Mongo so the web panel can show them."""

    def emit(self, record):
        if record.levelno < logging.WARNING or record.name.startswith(("pymongo", "aiogram.event")):
            return
        try:
            doc = {"at": datetime.now(timezone.utc), "level": record.levelname,
                   "msg": record.getMessage()[:1000]}
            if record.exc_info:
                doc["trace"] = "".join(traceback.format_exception(*record.exc_info))[-2500:]
            errors_col.insert_one(doc)
        except Exception:
            pass
