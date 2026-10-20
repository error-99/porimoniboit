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

    # user_seq: continue from the highest existing user number
    if not counters_col.find_one({"_id": "main"}):
        top = users_col.find_one({"id": {"$exists": True}}, sort=[("id", DESCENDING)])
        counters_col.update_one(
            {"_id": "main"},
            {"$setOnInsert": {"user_seq": int(top["id"]) if top else 0}},
            upsert=True,
        )
    logging.info("DB initialized (videos collection untouched)")


def next_seq(field: str) -> int:
    from pymongo import ReturnDocument
    doc = counters_col.find_one_and_update(
        {"_id": "main"}, {"$inc": {field: 1}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return int(doc[field])
