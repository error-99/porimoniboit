"""Choosing videos (category priority) and getting Telegram file_ids for albums.

The `videos` collection is only ever READ here."""
import asyncio
import logging
import random

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from pymongo import ASCENDING

from config import DUMP_CHAT_ID, DELETE_DUMP
from db import videos_col, files_col, bad_col, COUNTER_ID

UNCAT = "uncategorized"
_GONE = ("message to forward not found", "message can't be forwarded",
         "message_id_invalid", "message to copy not found")


class VideoGone(Exception):
    """The source message no longer exists in the channel."""


def bad_ids() -> set:
    return {d["_id"] for d in bad_col.find({}, {"_id": 1})}


def mark_bad(video: dict, reason: str):
    bad_col.update_one(
        {"_id": int(video["message_id"])},
        {"$set": {"channel_id": video.get("channel_id"), "reason": reason[:200]}},
        upsert=True,
    )


def _cat_filter(name: str) -> dict:
    if name == UNCAT:
        return {"category": {"$in": [None, ""]}}
    return {"category": name}


def pick_videos(exclude: set, count: int, st: dict) -> list:
    """Pick `count` unseen videos. Category chance = its priority (0 = never)."""
    base = {"_id": {"$ne": COUNTER_ID}, "message_id": {"$nin": list(exclude)}}
    counts = {}
    for g in videos_col.aggregate([{"$match": base},
                                   {"$group": {"_id": "$category", "n": {"$sum": 1}}}]):
        counts[g["_id"] or UNCAT] = counts.get(g["_id"] or UNCAT, 0) + g["n"]

    prio = st["category_priority"]
    picked, ex = [], set(exclude)
    for _ in range(count):
        cats = [c for c, n in counts.items() if n > 0 and prio.get(c, st["default_priority"]) > 0]
        if not cats:
            break
        cat = random.choices(cats, weights=[prio.get(c, st["default_priority"]) for c in cats])[0]
        q = {"_id": {"$ne": COUNTER_ID}, "message_id": {"$nin": list(ex)}, **_cat_filter(cat)}
        if st["mode"] == "random":
            docs = list(videos_col.aggregate([{"$match": q}, {"$sample": {"size": 1}}]))
            v = docs[0] if docs else None
        else:
            v = videos_col.find_one(q, sort=[("created_at", ASCENDING), ("id", ASCENDING)])
        if not v:
            counts[cat] = 0
            continue
        picked.append(v)
        ex.add(int(v["message_id"]))
        counts[cat] -= 1
    return picked


async def resolve(bot, video: dict):
    """-> (kind, file_id). Forwards to the dump chat once, then reads the cache."""
    cid, mid = int(video["channel_id"]), int(video["message_id"])
    key = {"channel_id": cid, "message_id": mid, "bot_id": bot.id}
    cached = files_col.find_one(key)
    if cached and cached.get("v") == 2:          # v1 entries lack video size/duration -> reload once
        video["_meta"] = cached.get("meta") or {}
        return cached["kind"], cached["file_id"]

    m = None
    for _ in range(2):
        try:
            m = await bot.forward_message(DUMP_CHAT_ID, cid, mid, disable_notification=True)
            break
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if any(x in str(e).lower() for x in _GONE):
                raise VideoGone(str(e))
            raise
    if m is None:
        raise RuntimeError("Telegram flood limit while reading video")

    meta = {}
    if m.video:
        kind, fid = "video", m.video.file_id
        meta = {k: getattr(m.video, k, None) for k in ("width", "height", "duration")}
        meta = {k: v for k, v in meta.items() if v}
    elif m.animation:
        kind, fid = "animation", m.animation.file_id
    elif m.document:
        kind, fid = "document", m.document.file_id
    else:
        kind, fid = "other", ""
    if DELETE_DUMP:
        try:
            await bot.delete_message(DUMP_CHAT_ID, m.message_id)
        except Exception:
            pass
    files_col.update_one(key, {"$set": {"kind": kind, "file_id": fid, "meta": meta, "v": 2}}, upsert=True)
    video["_meta"] = meta
    return kind, fid
