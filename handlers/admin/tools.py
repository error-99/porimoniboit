import asyncio
import logging
import time

from aiogram import Router
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply,
)
from aiogram.exceptions import (
    TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest,
)

from config import ADMIN_ID
from db import users_col
from .users import _safe_edit, _safe_answer
from .status import admin_home_text, admin_home_kb

router = Router()

PENDING_BROADCAST = {}   # {admin_id: {"from_chat_id":..., "message_id":...}}

# ============================================================
#  PERFORMANCE TUNING
# ============================================================
BATCH_SIZE             = 25   # concurrent users per wave (safe < 30/sec Telegram limit)
PROGRESS_REFRESH_EVERY = 1.5  # seconds between progress-message edits
INITIAL_GATHER_WAVE    = 25   # first wave size (get instant momentum)


# ============================================================
#  Progress helpers
# ============================================================
def make_progress_bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "░" * width
    filled = int((done / total) * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def fmt_seconds(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


async def progress_edit(cb, title, done, total, stats_lines, started_at):
    pct = (done / total * 100) if total > 0 else 0
    bar = make_progress_bar(done, total)
    elapsed = time.time() - started_at

    if done > 0 and total > done:
        eta = (elapsed / done) * (total - done)
        eta_str = fmt_seconds(eta)
    elif done >= total:
        eta_str = "done"
    else:
        eta_str = "…"

    text = (
        f"{title}\n\n"
        f"<code>{bar}</code> <b>{pct:.1f}%</b>\n"
        f"Progress: <b>{done}</b> / {total}\n"
        f"⏱ Elapsed: {fmt_seconds(elapsed)} &middot; ETA: {eta_str}\n\n"
        + "\n".join(stats_lines)
    )

    try:
        await cb.message.edit_text(text)
    except TelegramBadRequest as e:
        m = str(e)
        if "message is not modified" in m or "query is too old" in m or "query ID is invalid" in m:
            return
        logging.warning(f"progress_edit: {e}")
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
    except Exception as e:
        logging.warning(f"progress_edit: {e}")


# ============================================================
#  🧹 Clean blocked users — parallel
# ============================================================
@router.callback_query(lambda c: c.data == "adm_clean")
async def adm_clean_confirm(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, clean now", callback_data="adm_clean_yes")],
        [InlineKeyboardButton(text="❌ Cancel",        callback_data="adm_home")],
    ])
    await _safe_edit(
        cb,
        "🧹 <b>Clean Blocked Users</b>\n\n"
        "This will ping every user with a tiny typing action.\n"
        "Users who blocked the bot will be <b>deleted from the database</b>.\n\n"
        "⚠️ Cannot be undone. Continue?",
        kb,
    )


@router.callback_query(lambda c: c.data == "adm_clean_yes")
async def adm_clean_run(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb, "Scanning…")

    started = time.time()

    # ---- 1. snapshot user IDs (single query, no cursor round-trips) ----
    all_ids = [u["_id"] for u in users_col.find({}, {"_id": 1})]
    total = len(all_ids)
    if total == 0:
        await _safe_edit(
            cb, "🧹 No users in database.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")]
            ]),
        )
        return

    checked  = 0
    blocked  = 0
    deleted  = 0
    failed   = 0
    skipped  = 0
    admin_uid = ADMIN_ID

    sem = asyncio.Semaphore(BATCH_SIZE)

    async def check_one(uid):
        nonlocal checked, blocked, deleted, failed, skipped
        if uid == admin_uid:
            skipped += 1
            checked += 1
            return
        async with sem:
            try:
                await cb.bot.send_chat_action(uid, "typing")
            except TelegramForbiddenError:
                blocked += 1
                try:
                    users_col.delete_one({"_id": uid})
                    deleted += 1
                except Exception:
                    failed += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    await cb.bot.send_chat_action(uid, "typing")
                except TelegramForbiddenError:
                    blocked += 1
                    try:
                        users_col.delete_one({"_id": uid})
                        deleted += 1
                    except Exception:
                        failed += 1
                except Exception:
                    failed += 1
            except Exception:
                failed += 1
            checked += 1

    # ---- 2. process in parallel waves ----
    last_refresh = 0.0
    i = 0
    while i < total:
        batch = all_ids[i:i + BATCH_SIZE]
        await asyncio.gather(*(check_one(uid) for uid in batch))
        i += BATCH_SIZE

        now = time.time()
        if now - last_refresh >= PROGRESS_REFRESH_EVERY or i >= total:
            last_refresh = now
            await progress_edit(
                cb,
                "🧹 <b>Cleaning blocked users…</b>",
                checked, total,
                [
                    f"🚫 Blocked detected: <b>{blocked}</b>",
                    f"🗑 Deleted from DB: <b>{deleted}</b>",
                    f"⚠️ Errors: <b>{failed}</b>",
                    f"⏭ Skipped (admin): <b>{skipped}</b>",
                ],
                started,
            )

    await _safe_edit(
        cb,
        f"🧹 <b>Clean Complete</b>\n\n"
        f"🔍 Checked: <b>{checked}</b>\n"
        f"🚫 Blocked detected: <b>{blocked}</b>\n"
        f"🗑 Deleted from DB: <b>{deleted}</b>\n"
        f"⚠️ Errors: <b>{failed}</b>\n"
        f"⏭ Skipped (admin): <b>{skipped}</b>\n"
        f"⏱ Took: {fmt_seconds(time.time() - started)}",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")]
        ]),
    )


# ============================================================
#  📢 Broadcast — parallel
# ============================================================
@router.callback_query(lambda c: c.data == "adm_broadcast")
async def adm_broadcast_start(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)

    await _safe_edit(
        cb,
        "📢 <b>Broadcast</b>\n\n"
        "Reply to this message with the content to broadcast.\n"
        "You can send: text, photo, video, document, sticker.\n\n"
        "I'll copy your reply as-is to every user.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="adm_home")]
        ]),
    )

    await cb.message.answer(
        "📝 <b>Reply with the broadcast content</b> to this message.",
        reply_markup=ForceReply(selective=True,
                                input_field_placeholder="Type your broadcast…"),
    )


@router.message(
    lambda m: m.from_user.id == ADMIN_ID
              and m.reply_to_message is not None
              and m.reply_to_message.text is not None
              and "Reply with the broadcast content" in (m.reply_to_message.text or "")
)
async def adm_broadcast_capture(msg: Message):
    PENDING_BROADCAST[ADMIN_ID] = {
        "from_chat_id": msg.chat.id,
        "message_id":   msg.message_id,
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Send to ALL users", callback_data="adm_broadcast_go")],
        [InlineKeyboardButton(text="❌ Cancel",            callback_data="adm_broadcast_cancel")],
    ])
    await msg.answer(
        "✅ Content captured.\n\n"
        "Ready to send to <b>all users</b>?\n\n"
        "Progress will be shown live.",
        reply_markup=kb,
    )


@router.callback_query(lambda c: c.data == "adm_broadcast_cancel")
async def adm_broadcast_cancel(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)
    PENDING_BROADCAST.pop(ADMIN_ID, None)
    await _safe_edit(
        cb,
        "❌ Broadcast cancelled.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")]
        ]),
    )


@router.callback_query(lambda c: c.data == "adm_broadcast_go")
async def adm_broadcast_go(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    pending = PENDING_BROADCAST.pop(ADMIN_ID, None)
    if not pending:
        await _safe_answer(cb, "Nothing to broadcast", show_alert=True)
        return

    await _safe_answer(cb, "Broadcasting…")
    started = time.time()

    # ---- snapshot IDs, exclude admin ----
    all_ids = [
        u["_id"] for u in users_col.find(
            {"_id": {"$ne": ADMIN_ID}},
            {"_id": 1},
        )
    ]
    total = len(all_ids)
    if total == 0:
        await _safe_edit(
            cb, "📢 No users to broadcast to.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")]
            ]),
        )
        return

    sent    = 0
    failed  = 0
    blocked = 0
    deleted = 0
    done    = 0

    sem = asyncio.Semaphore(BATCH_SIZE)
    blocked_uids = []   # batch-delete at the end

    async def send_one(uid):
        nonlocal sent, failed, blocked, done
        async with sem:
            try:
                await cb.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=pending["from_chat_id"],
                    message_id=pending["message_id"],
                    protect_content=True,
                )
                sent += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    await cb.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=pending["from_chat_id"],
                        message_id=pending["message_id"],
                        protect_content=True,
                    )
                    sent += 1
                except TelegramForbiddenError:
                    blocked += 1
                    blocked_uids.append(uid)
                except Exception:
                    failed += 1
            except TelegramForbiddenError:
                blocked += 1
                blocked_uids.append(uid)
            except Exception as e:
                logging.warning(f"broadcast {uid}: {e}")
                failed += 1
            done += 1

    # ---- process in parallel waves ----
    last_refresh = 0.0
    i = 0
    while i < total:
        batch = all_ids[i:i + BATCH_SIZE]
        await asyncio.gather(*(send_one(uid) for uid in batch))
        i += BATCH_SIZE

        # batch-delete blocked uids after each wave
        if blocked_uids:
            try:
                users_col.delete_many({"_id": {"$in": blocked_uids}})
                deleted += len(blocked_uids)
            except Exception as e:
                logging.warning(f"bulk delete blocked: {e}")
            blocked_uids.clear()

        now = time.time()
        if now - last_refresh >= PROGRESS_REFRESH_EVERY or i >= total:
            last_refresh = now
            await progress_edit(
                cb,
                "📢 <b>Broadcasting…</b>",
                done, total,
                [
                    f"✅ Sent: <b>{sent}</b>",
                    f"🚫 Blocked: <b>{blocked}</b>",
                    f"🗑 Deleted from DB: <b>{deleted}</b>",
                    f"⚠️ Failed: <b>{failed}</b>",
                ],
                started,
            )

    # final cleanup of any remaining blocked uids
    if blocked_uids:
        try:
            users_col.delete_many({"_id": {"$in": blocked_uids}})
            deleted += len(blocked_uids)
        except Exception:
            pass

    await _safe_edit(
        cb,
        f"📢 <b>Broadcast complete</b>\n\n"
        f"👥 Total tried: <b>{total}</b>\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"🚫 Blocked: <b>{blocked}</b>\n"
        f"🗑 Deleted from DB: <b>{deleted}</b>\n"
        f"⚠️ Failed: <b>{failed}</b>\n"
        f"⏱ Took: {fmt_seconds(time.time() - started)}",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")]
        ]),
    )
