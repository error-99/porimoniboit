import logging

from aiogram import Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import ADMIN_ID
from db import users_col
from .users import (
    user_detail_text, user_detail_kb,
    _safe_edit, _safe_answer,
)

router = Router()


@router.callback_query(lambda c: c.data and c.data.startswith("adm_del:"))
async def adm_del(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)

    uid = int(cb.data.split(":")[1])
    u = users_col.find_one({"_id": uid})
    if not u:
        await _safe_answer(cb, "User not found", show_alert=True)
        return

    hist = u.get("views", {}).get("history", []) or []
    if not hist:
        await _safe_answer(cb, "No history", show_alert=True)
        return

    last = hist[-1]
    smid = last.get("sent_message_id") if isinstance(last, dict) else None
    if not smid:
        await _safe_answer(cb, "No sent message id", show_alert=True)
        return

    try:
        await cb.bot.delete_message(uid, smid)
        users_col.update_one(
            {"_id": uid},
            {"$set": {"views.history": hist[:-1]}},
        )
        await _safe_answer(cb, "✅ Deleted from user chat")
    except Exception as e:
        logging.warning(f"adm_del failed: {e}")
        await _safe_answer(cb, f"❌ Failed: {e}", show_alert=True)
        return

    u = users_col.find_one({"_id": uid})
    await _safe_edit(cb, user_detail_text(u), user_detail_kb(u))


@router.callback_query(lambda c: c.data and c.data.startswith("adm_delall:"))
async def adm_delall(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)

    uid = int(cb.data.split(":")[1])
    u = users_col.find_one({"_id": uid})
    if not u:
        await _safe_answer(cb, "User not found", show_alert=True)
        return

    hist = u.get("views", {}).get("history", []) or []
    if not hist:
        await _safe_answer(cb, "No history", show_alert=True)
        return

    deleted = 0
    for entry in hist:
        smid = entry.get("sent_message_id") if isinstance(entry, dict) else None
        if not smid:
            continue
        try:
            await cb.bot.delete_message(uid, smid)
            deleted += 1
        except Exception as e:
            logging.warning(f"del msg {smid}: {e}")

    users_col.update_one(
        {"_id": uid},
        {"$set": {"views.history": []}},
    )
    await _safe_answer(cb, f"✅ Deleted {deleted} message(s)")

    u = users_col.find_one({"_id": uid})
    await _safe_edit(cb, user_detail_text(u), user_detail_kb(u))


@router.callback_query(lambda c: c.data and c.data.startswith("adm_blk:"))
async def adm_blk(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)

    uid = int(cb.data.split(":")[1])
    u = users_col.find_one({"_id": uid})
    if not u:
        await _safe_answer(cb, "User not found", show_alert=True)
        return

    new_state = not u.get("blocked", False)
    users_col.update_one({"_id": uid}, {"$set": {"blocked": new_state}})
    await _safe_answer(cb, "🚫 Blocked" if new_state else "✅ Unblocked")

    u = users_col.find_one({"_id": uid})
    await _safe_edit(cb, user_detail_text(u), user_detail_kb(u))


@router.callback_query(lambda c: c.data == "adm_purge")
async def adm_purge(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚠️ YES — DELETE EVERYTHING",
            callback_data="adm_purge_yes",
        )],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="adm_home")],
    ])
    await _safe_edit(
        cb,
        "⚠️ <b>DANGER ZONE</b>\n\n"
        "This will:\n"
        "• Delete every forwarded video from <b>every user's chat</b>\n"
        "• Clear every user's history\n"
        "• Cannot be undone\n\n"
        "Continue?",
        kb,
    )


@router.callback_query(lambda c: c.data == "adm_purge_yes")
async def adm_purge_yes(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb, "Purging…")

    total_deleted = 0
    total_users   = 0
    failed        = 0

    cursor = users_col.find(
        {"views.history.0": {"$exists": True}},
        {"views.history": 1},
    )

    for u in cursor:
        uid  = u["_id"]
        hist = u.get("views", {}).get("history", []) or []
        for entry in hist:
            smid = entry.get("sent_message_id") if isinstance(entry, dict) else None
            if not smid:
                continue
            try:
                await cb.bot.delete_message(uid, smid)
                total_deleted += 1
            except Exception as e:
                failed += 1
                logging.warning(f"purge del {uid}/{smid}: {e}")

        users_col.update_one(
            {"_id": uid},
            {"$set": {"views.history": [], "views.last_video": None}},
        )
        total_users += 1

    await _safe_edit(
        cb,
        f"✅ <b>Purge complete</b>\n\n"
        f"👥 Users cleared: <b>{total_users}</b>\n"
        f"🗑 Messages deleted: <b>{total_deleted}</b>\n"
        f"⚠️ Failed: <b>{failed}</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")]
        ]),
    )
