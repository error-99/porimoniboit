import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from pymongo import DESCENDING

from config import ADMIN_ID
from db import users_col

router = Router()


# ---------- Safe helpers ----------
async def _safe_edit(cb: CallbackQuery, text: str, kb):
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        msg = str(e)
        if "message is not modified" in msg:
            return
        if "query is too old" in msg or "query ID is invalid" in msg:
            return
        logging.warning(f"_safe_edit: {e}")
    except Exception as e:
        logging.warning(f"_safe_edit: {e}")


async def _safe_answer(cb: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await cb.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        msg = str(e)
        if "query is too old" in msg or "query ID is invalid" in msg:
            return
        logging.warning(f"_safe_answer: {e}")
    except Exception as e:
        logging.warning(f"_safe_answer: {e}")


# ---------- Detail view ----------
def user_detail_text(u) -> str:
    s = u.get("stats", {})
    v = u.get("views", {})
    hist = v.get("history", []) or []
    last = hist[-1] if hist else None
    last_vid = last.get("video_id") if isinstance(last, dict) else "-"
    return (
        f"👤 <b>{u.get('name','?')}</b>\n"
        f"UID: <code>{u['_id']}</code>\n"
        f"User#: {u.get('id')}\n"
        f"Username: @{u.get('username','-')}\n"
        f"Blocked: <b>{u.get('blocked', False)}</b>\n"
        f"Processing: {u.get('processing', False)}\n\n"
        f"📊 Today: {s.get('today',0)} | Total: {s.get('total',0)}\n"
        f"🎁 Bonus date: {s.get('bonus_date') or '-'}\n"
        f"📦 History: {len(hist)} items\n"
        f"🎬 Last video msg: {last_vid}\n"
        f"🗑 Deleted ids: {len(v.get('deleted_ids', []) or [])}\n"
        f"🕒 Last active: {u.get('last_active')}"
    )


def user_detail_kb(u) -> InlineKeyboardMarkup:
    uid = u["_id"]
    block_label = "✅ Unblock" if u.get("blocked") else "🚫 Block"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Delete Last Media",  callback_data=f"adm_del:{uid}")],
        [InlineKeyboardButton(text="🗑 Delete ALL Media",   callback_data=f"adm_delall:{uid}")],
        [InlineKeyboardButton(text=block_label,             callback_data=f"adm_blk:{uid}")],
        [InlineKeyboardButton(text="🔄 Refresh",            callback_data=f"adm_u:{uid}")],
        [InlineKeyboardButton(text="🔙 Back",               callback_data="adm_users:0")],
    ])


# ---------- Users list ----------
def _get_users_page(page: int, per_page: int = 5):
    total = users_col.count_documents({})
    pages = max(1, (total + per_page - 1) // per_page)
    users_list = list(
        users_col.find({})
        .sort("last_active", DESCENDING)
        .skip(page * per_page)
        .limit(per_page)
    )
    return users_list, page, pages, total


def _users_page_kb(users_list, page: int, pages: int, total: int):
    rows = []
    for u in users_list:
        name = (u.get("name") or "?")[:18]
        uid  = u["_id"]
        flag = "🚫 " if u.get("blocked") else ""
        rows.append([InlineKeyboardButton(
            text=f"{flag}{name} | {uid}",
            callback_data=f"adm_u:{uid}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm_users:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="adm_noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm_users:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 Home", callback_data="adm_home")])

    text = f"👥 <b>Users</b> (page {page+1}/{pages})\nTotal: <b>{total}</b>"
    return InlineKeyboardMarkup(inline_keyboard=rows), text


async def _render_users_page(msg: Message, page: int = 0):
    users_list, page, pages, total = _get_users_page(page)
    kb, text = _users_page_kb(users_list, page, pages, total)
    await msg.answer(text, reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("adm_users:"))
async def adm_users(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)
    page = int(cb.data.split(":")[1])
    users_list, page, pages, total = _get_users_page(page)
    kb, text = _users_page_kb(users_list, page, pages, total)
    await _safe_edit(cb, text, kb)


@router.callback_query(lambda c: c.data and c.data.startswith("adm_u:"))
async def adm_user_detail(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await _safe_answer(cb)

    uid = int(cb.data.split(":")[1])
    u = users_col.find_one({"_id": uid})
    if not u:
        await _safe_answer(cb, "User not found", show_alert=True)
        return
    await _safe_edit(cb, user_detail_text(u), user_detail_kb(u))
