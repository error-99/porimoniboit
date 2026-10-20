from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply,
)

from config import ADMIN_ID
from db import users_col, videos_col, COUNTER_ID
from helpers import ADMIN_STATUS_TEXT, ADMIN_USERS_TEXT, admin_kb

router = Router()


def admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Recent Users",         callback_data="adm_users:0")],
        [InlineKeyboardButton(text="🧹 Clean Blocked Users",  callback_data="adm_clean")],
        [InlineKeyboardButton(text="📢 Broadcast",            callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🗑 Purge All History",    callback_data="adm_purge")],
    ])


async def admin_home_text() -> str:
    total_users  = users_col.count_documents({})
    total_videos = videos_col.count_documents({"_id": {"$ne": COUNTER_ID}})

    agg = list(users_col.aggregate([
        {"$group": {
            "_id": None,
            "today_sends": {"$sum": "$stats.today"},
            "all_sends":   {"$sum": "$stats.total"},
        }}
    ]))
    today_sends = agg[0]["today_sends"] if agg else 0
    all_sends   = agg[0]["all_sends"]   if agg else 0

    total_sent_msgs = 0
    for u in users_col.find({"views.history.0": {"$exists": True}},
                            {"views.history": 1}):
        total_sent_msgs += len(u.get("views", {}).get("history", []) or [])

    return (
        f"🛠 <b>Admin Panel</b>\n\n"
        f"👥 Users: <b>{total_users}</b>\n"
        f"🎬 Videos: <b>{total_videos}</b>\n"
        f"📤 Today sends: <b>{today_sends}</b>\n"
        f"📊 Total sends: <b>{all_sends}</b>\n"
        f"💬 Forwarded msgs in chats: <b>{total_sent_msgs}</b>\n"
        f"🚫 Blocked: <b>{users_col.count_documents({'blocked': True})}</b>"
    )


@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.answer(await admin_home_text(),
                     reply_markup=admin_home_kb())


@router.message(lambda m: m.text == ADMIN_STATUS_TEXT)
async def btn_status(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.answer(await admin_home_text(),
                     reply_markup=admin_home_kb())


@router.message(lambda m: m.text == ADMIN_USERS_TEXT)
async def btn_users(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    from .users import _render_users_page
    await _render_users_page(msg, page=0)


@router.callback_query(lambda c: c.data == "adm_home")
async def adm_home(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    from .users import _safe_edit
    await _safe_edit(cb, await admin_home_text(), admin_home_kb())


@router.callback_query(lambda c: c.data == "adm_noop")
async def adm_noop(cb: CallbackQuery):
    await cb.answer()
