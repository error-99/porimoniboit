import logging

from pymongo import MongoClient, ASCENDING, DESCENDING

from config import MONGO_URI, MONGO_DB

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[MONGO_DB]

# ---- existing collections ----
users_col = db["users"]
videos_col = db["videos"]          # READ-ONLY for this project. Never written to.

# ---- new collections (all created/owned by this project) ----
settings_col = db["settings"]       # _id "main" = settings, _id "heartbeat" = bot alive ping
tasks_col = db["tasks"]             # join channel / group / custom link tasks
files_col = db["video_files"]       # cache: (channel_id, message_id) -> file_id
bad_col = db["bad_videos"]          # videos that no longer exist in the channel
broadcasts_col = db["broadcasts"]   # jobs created by the web panel
errors_col = db["error_log"]        # warnings/errors shown in the web panel
counters_col = db["counters"]       # user_seq lives here now (was inside `videos`)

COUNTER_ID = "__counter"            # legacy counter doc inside `videos` — only ever filtered out


def init_db():
    users_col.create_index([("id", ASCENDING)], unique=True, sparse=True)
    users_col.create_index([("last_active", DESCENDING)])
    files_col.create_index(
        [("channel_id", ASCENDING), ("message_id", ASCENDING), ("bot_id", ASCENDING)],
        unique=True,
    )
    tasks_col.create_index([("order", ASCENDING)])
    broadcasts_col.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    errors_col.create_index([("at", DESCENDING)], expireAfterSeconds=30 * 24 * 3600)

    # user_seq: continue from the highest existing user number (and heal a non-numeric value)
    cur = counters_col.find_one({"_id": "main"})
    if cur is None or not _is_int(cur.get("user_seq")):
        _repair_counter("user_seq")
    logging.info("DB initialized (videos collection untouched)")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _repair_counter(field: str):
    """Make counters.main[field] a real integer >= the highest existing user number.
    Fixes values stored as strings ("123") or missing, which break $inc."""
    doc = counters_col.find_one({"_id": "main"}) or {}
    try:
        base = int(float(str(doc.get(field, 0)).strip()))
    except (TypeError, ValueError):
        base = 0
    top = 0
    for u in users_col.find({"id": {"$exists": True}}, {"id": 1}):
        try:
            top = max(top, int(float(str(u["id"]).strip())))
        except (TypeError, ValueError):
            pass
    counters_col.update_one({"_id": "main"}, {"$set": {field: max(base, top)}}, upsert=True)
    logging.warning(f"counter '{field}' repaired -> {max(base, top)} (was {doc.get(field)!r})")


def next_seq(field: str) -> int:
    from pymongo import ReturnDocument
    from pymongo.errors import OperationFailure

    def _inc():
        return counters_col.find_one_and_update(
            {"_id": "main"}, {"$inc": {field: 1}},
            upsert=True, return_document=ReturnDocument.AFTER)
    try:
        doc = _inc()
    except OperationFailure:          # e.g. "Cannot apply $inc to a value of non-numeric type"
        _repair_counter(field)
        doc = _inc()
    return int(doc[field])
