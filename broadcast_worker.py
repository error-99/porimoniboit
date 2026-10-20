"""Runs broadcasts queued from the web panel, and writes a heartbeat so the panel
can show whether the bot is online."""
import asyncio
import logging
import time

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import users_col, broadcasts_col, settings_col
from helpers import now_utc

WAVE = 25          # parallel sends per wave (Telegram allows ~30 msgs/sec)
REFRESH = 1.5      # seconds between progress writes


async def heartbeat():
    while True:
        try:
            settings_col.update_one({"_id": "heartbeat"}, {"$set": {"at": now_utc()}}, upsert=True)
        except Exception:
            pass
        await asyncio.sleep(20)


async def _send(bot, uid: int, job: dict):
    kb = None
    if job.get("button_text") and job.get("button_url"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=job["button_text"], url=job["button_url"])]])
    text, media, kind = job.get("text") or "", job.get("media_url"), job.get("media_type")
    protect = bool(job.get("protect", True))
    if media and kind == "photo":
        return await bot.send_photo(uid, media, caption=text or None, reply_markup=kb, protect_content=protect)
    if media and kind == "video":
        return await bot.send_video(uid, media, caption=text or None, reply_markup=kb, protect_content=protect)
    return await bot.send_message(uid, text, reply_markup=kb, protect_content=protect,
                                  disable_web_page_preview=True)


async def _run(bot, job: dict):
    jid = job["_id"]
    ids = [u["_id"] for u in users_col.find(
        {"blocked": {"$ne": True}, "unreachable": {"$ne": True}}, {"_id": 1})]
    total = len(ids)
    c = {"sent": 0, "blocked": 0, "failed": 0, "done": 0}
    broadcasts_col.update_one({"_id": jid}, {"$set": {"total": total, **c}})
    unreachable, last, cancelled = [], 0.0, False

    async def one(uid):
        for attempt in range(2):
            try:
                await _send(bot, uid, job)
                c["sent"] += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                c["blocked"] += 1
                unreachable.append(uid)
                break
            except Exception as e:
                logging.warning(f"broadcast to {uid}: {e}")
                c["failed"] += 1
                break
        else:
            c["failed"] += 1
        c["done"] += 1

    for i in range(0, total, WAVE):
        await asyncio.gather(*(one(u) for u in ids[i:i + WAVE]))
        if unreachable:
            users_col.update_many({"_id": {"$in": unreachable}}, {"$set": {"unreachable": True}})
            unreachable.clear()
        if time.time() - last >= REFRESH or i + WAVE >= total:
            last = time.time()
            cur = broadcasts_col.find_one_and_update({"_id": jid}, {"$set": c})
            if cur and cur.get("status") == "cancelling":
                cancelled = True
                break
    broadcasts_col.update_one({"_id": jid}, {"$set": {
        **c, "status": "cancelled" if cancelled else "done", "finished_at": now_utc()}})


async def broadcast_worker(bot):
    # a job still "running" at startup was cut off by a restart
    broadcasts_col.update_many({"status": "running"}, {"$set": {"status": "interrupted"}})
    while True:
        try:
            job = broadcasts_col.find_one_and_update(
                {"status": "pending"}, {"$set": {"status": "running", "started_at": now_utc()}},
                sort=[("created_at", 1)])
            if job:
                await _run(bot, job)
            else:
                await asyncio.sleep(3)
        except Exception as e:
            logging.exception(f"broadcast worker error: {e}")
            await asyncio.sleep(5)
