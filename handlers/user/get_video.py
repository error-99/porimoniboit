import asyncio
import logging
from datetime import timezone, timedelta

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message, InputMediaVideo

import picker
from config import ADMIN_ID
from db import users_col
from helpers import (
    now_utc, today_str, render, GET_VIDEO_TEXT,
    get_not_joined, force_join_kb, get_bonus_limit,
)
from settings import get_settings

router = Router()

MAX_REPLACEMENTS = 10   # extra videos tried when some in an album turn out to be gone


async def auto_cleanup_old_history(bot, uid: int, history: list, hours: int) -> list:
    if not history or hours <= 0:
        return history or []
    cutoff = now_utc() - timedelta(hours=hours)
    kept, changed = [], False
    for entry in history:
        at = entry.get("at") if isinstance(entry, dict) else None
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at is None or at >= cutoff:
            kept.append(entry)
            continue
        smid = entry.get("sent_message_id")
        if smid:
            try:
                await bot.delete_message(uid, smid)
            except Exception as e:
                logging.info(f"auto-cleanup skip {uid}/{smid}: {e}")
        changed = True
    if changed:
        try:
            users_col.update_one({"_id": uid}, {"$set": {"views.history": kept}})
        except Exception as e:
            logging.warning(f"auto-cleanup db update failed: {e}")
    return kept


# ---------------- sending ----------------
async def _call(fn, *a, caption=None, **kw):
    """Send with caption; retry once on flood-wait; drop HTML if the caption is invalid."""
    for attempt in range(2):
        try:
            return await fn(*a, caption=caption, **kw)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if caption and "parse entities" in str(e).lower():
                return await fn(*a, caption=caption, parse_mode=None, **kw)
            raise
    raise RuntimeError("Telegram flood limit")


def _meta(item) -> dict:
    """width/height/duration saved from the original video."""
    return (item[0].get("_meta") or {})


async def _send_one(bot, chat, item, caption):
    v, kind, fid = item
    if kind == "video":
        # supports_streaming=True is what lets the video play while downloading
        return [await _call(bot.send_video, chat, fid, caption=caption, protect_content=True,
                            supports_streaming=True, **_meta(item))]
    m = await bot.forward_message(chat, int(v["channel_id"]), int(v["message_id"]),
                                  protect_content=True)
    return [m]


async def _send_batch(bot, chat, items, caption):
    """Returns [(item, Message), ...] for everything that was delivered."""
    if len(items) >= 2 and all(i[1] == "video" for i in items):
        try:
            media = [InputMediaVideo(media=i[2], caption=caption if n == 0 else None,
                                     supports_streaming=True, **_meta(i))
                     for n, i in enumerate(items)]
            try:
                msgs = await bot.send_media_group(chat, media, protect_content=True)
            except TelegramBadRequest as e:
                if "parse entities" not in str(e).lower():
                    raise
                for m in media:
                    m.parse_mode = None
                msgs = await bot.send_media_group(chat, media, protect_content=True)
            return list(zip(items, msgs))
        except Exception as e:
            logging.warning(f"album send failed, sending one by one: {e}")

    out = []
    for n, item in enumerate(items):
        try:
            for m in await _send_one(bot, chat, item, caption if n == 0 else None):
                out.append((item, m))
        except Exception as e:
            logging.warning(f"send failed for video {item[0].get('message_id')}: {e}")
            if isinstance(e, TelegramBadRequest) and any(
                    x in str(e).lower() for x in picker._GONE):
                picker.mark_bad(item[0], str(e))
    return out


async def _forward_collect(bot, chat, exclude: set, want: int, st: dict):
    """Forward `want` original videos. Missing ones are skipped and replaced.
    Returns ([((video, "video", ""), Message), ...], videos_tried)."""
    sent, tries = [], 0
    while len(sent) < want and tries < want + MAX_REPLACEMENTS:
        cands = picker.pick_videos(exclude, want - len(sent), st)
        if not cands:
            break
        for v in cands:
            exclude.add(int(v["message_id"]))
            tries += 1
            for attempt in range(2):
                try:
                    m = await bot.forward_message(chat, int(v["channel_id"]), int(v["message_id"]),
                                                  protect_content=True)
                    sent.append(((v, "video", ""), m))
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramBadRequest as e:
                    if any(x in str(e).lower() for x in picker._GONE):
                        picker.mark_bad(v, str(e))
                        logging.warning(f"Video {v.get('message_id')} is gone from channel — skipped")
                    else:
                        logging.warning(f"forward failed for {v.get('message_id')}: {e}")
                    break
                except Exception as e:
                    logging.warning(f"forward failed for {v.get('message_id')}: {e}")
                    break
    return sent, tries


# ---------------- handler ----------------
@router.message(F.text == GET_VIDEO_TEXT)
async def on_get_video(msg: Message):
    uid, chat = msg.from_user.id, msg.chat.id
    is_admin = (uid == ADMIN_ID)
    st = get_settings()

    user = users_col.find_one({"_id": uid})
    if not user and not is_admin:
        await msg.answer("আবার /start দিন")
        return
    if user and user.get("blocked"):
        await msg.answer("⛔ ব্লকড")
        return
    if user and user.get("processing"):
        await msg.answer("⏳ একটু অপেক্ষা করুন")
        return

    if user:
        users_col.update_one({"_id": uid}, {"$set": {"processing": True}})

    try:
        today = today_str()
        remaining = None   # None = unlimited

        if user:
            if user.get("date") != today:
                users_col.update_one({"_id": uid}, {"$set": {"stats.today": 0, "date": today}})
                user.setdefault("stats", {})["today"] = 0

            if not is_admin and st["cooldown_seconds"] > 0:
                last = user.get("last_active")
                if last:
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    elapsed = (now_utc() - last).total_seconds()
                    if elapsed < st["cooldown_seconds"]:
                        await msg.answer(f"⏱ {int(st['cooldown_seconds'] - elapsed)}s পর চেষ্টা করুন")
                        return

            raw_hist = user.get("views", {}).get("history", []) or []
            user.setdefault("views", {})["history"] = await auto_cleanup_old_history(
                msg.bot, uid, raw_hist, st["auto_delete_hours"])

            if not is_admin and st["daily_limit"] > 0:
                stats = user.get("stats", {})
                bonus_claimed = (stats.get("bonus_date") == today)
                effective = st["daily_limit"] + (get_bonus_limit() if bonus_claimed else 0)
                remaining = effective - stats.get("today", 0)

                if remaining <= 0:
                    if bonus_claimed:
                        await msg.answer("📵 <b>আজকের লিমিট শেষ!</b>\n\nআগামীকাল আবার চেষ্টা করুন।")
                        return
                    not_joined = await get_not_joined(msg.bot, uid)
                    await msg.answer(
                        f"📵 <b>আজকের লিমিট শেষ!</b>\n\n"
                        f"🎁 <b>+{get_bonus_limit()} ভিডিও</b> বোনাস পেতে:\n"
                        f"১️⃣ নিচের সব চ্যানেল/গ্রুপ জয়েন করুন\n"
                        f"২️⃣ 🚀 <b>Share Bot</b> করে বন্ধুদের পাঠান\n"
                        f"৩️⃣ ✅ <b>I Joined</b> চাপুন\n\n"
                        f"তারপরই বোনাস অ্যাক্টিভ হবে!",
                        reply_markup=force_join_kb(not_joined),
                    )
                    return

        # how many videos this tap should deliver
        want = st["album_size"] if st["album_enabled"] else 1   # videos per tap (an "album" of N)
        if remaining is not None:
            want = min(want, remaining)      # an album never goes past the daily limit

        history_raw = (user or {}).get("views", {}).get("history", []) or []
        seen = {h.get("video_id") if isinstance(h, dict) else h for h in history_raw}
        seen |= set((user or {}).get("views", {}).get("deleted_ids", []) or [])
        seen.discard(None)
        exclude = seen | picker.bad_ids()

        stats = (user or {}).get("stats", {})

        def make_caption(n: int):
            left = "∞" if remaining is None else max(0, remaining - n)
            return render(st["caption_template"], name=msg.from_user.first_name or "",
                          left=left, today=stats.get("today", 0) + n,
                          total=stats.get("total", 0) + n)[:1000] or None

        extra_ids = []      # text messages sent next to the videos (deleted with them later)
        if st["send_method"] == "forward":
            # forward the ORIGINAL videos from the channel — same quality, same playback
            sent, tries = await _forward_collect(msg.bot, chat, exclude, want, st)
            caption = make_caption(len(sent)) if sent else None
            if caption:     # a forward cannot carry new text, so it follows as its own message
                try:
                    extra_ids.append((await msg.bot.send_message(chat, caption)).message_id)
                except Exception as e:
                    logging.warning(f"caption send failed: {e}")
        else:
            # album: re-send by file_id so Telegram can group them into one album
            ready, tries = [], 0
            while len(ready) < want and tries < want + MAX_REPLACEMENTS:
                cands = picker.pick_videos(exclude, want - len(ready), st)
                if not cands:
                    break
                for v in cands:
                    exclude.add(int(v["message_id"]))
                    tries += 1
                    try:
                        kind, fid = await picker.resolve(msg.bot, v)
                        ready.append((v, kind, fid))
                    except picker.VideoGone as e:
                        picker.mark_bad(v, str(e))
                        logging.warning(f"Video {v.get('message_id')} is gone from channel — skipped")
                    except Exception as e:
                        logging.warning(f"Could not load video {v.get('message_id')}: {e}")
            sent = await _send_batch(msg.bot, chat, ready, make_caption(len(ready))) if ready else []

        if not sent:
            text = ("📭 <b>সব ভিডিও দেখা হয়ে গেছে!</b>" if tries == 0
                    else "⚠️ এই মুহূর্তে ভিডিও পাঠানো যাচ্ছে না। একটু পরে চেষ্টা করুন।")
            await msg.bot.send_message(chat, text)
            return

        if user:
            if is_admin:
                users_col.update_one({"_id": uid}, {"$set": {"last_active": now_utc()}})
            else:
                users_col.update_one({"_id": uid}, {
                    "$set": {"views.last_video": int(sent[-1][0][0]["message_id"]),
                             "last_active": now_utc()},
                    "$push": {"views.history": {"$each": [
                        {"video_id": int(it[0]["message_id"]), "sent_message_id": m.message_id,
                         "at": now_utc()} for it, m in sent] + [
                        {"video_id": None, "sent_message_id": i, "at": now_utc()} for i in extra_ids]}},
                    "$inc": {"stats.today": len(sent), "stats.total": len(sent)},
                })

    except Exception as e:
        logging.exception(f"get_video error: {e}")
        try:
            await msg.answer("❌ সমস্যা হয়েছে")
        except Exception:
            pass
    finally:
        if user:
            users_col.update_one({"_id": uid}, {"$set": {"processing": False}})
