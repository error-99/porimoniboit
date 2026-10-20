import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import ADMIN_ID
from db import users_col, next_seq
from helpers import now_utc, today_str, main_kb, admin_kb, render
from settings import get_settings

router = Router()


@router.message(CommandStart())
async def cmd_start(msg: Message):
    uid = msg.from_user.id
    name = msg.from_user.first_name or "User"
    username = msg.from_user.username or "-"
    is_admin = (uid == ADMIN_ID)

    existing = users_col.find_one({"_id": uid}, {"id": 1, "blocked": 1})

    # a banned user stays banned — /start must not lift the ban
    if existing and existing.get("blocked") and not is_admin:
        await msg.answer("⛔ ব্লকড")
        return

    if existing:
        users_col.update_one(
            {"_id": uid},
            {"$set": {"name": name, "username": username,
                      "last_active": now_utc(), "unreachable": False}},
        )
    else:
        new_id = next_seq("user_seq")
        users_col.insert_one({
            "_id": uid, "id": new_id, "name": name, "username": username,
            "blocked": False, "processing": False,
            "views": {"last_video": None, "history": [], "deleted_ids": []},
            "stats": {"today": 0, "total": 0, "bonus_date": None},
            "last_active": now_utc(), "date": today_str(),
            "joined_at": now_utc(),
        })
        logging.info(f"New user id={new_id} uid={uid}")

        if not is_admin:
            try:
                await msg.bot.send_message(
                    ADMIN_ID,
                    f"🆕 <b>New User</b>\n\nName: <b>{name}</b>\nUsername: @{username}\n"
                    f"UID: <code>{uid}</code>\nUser#: <b>{new_id}</b>",
                )
            except Exception as e:
                logging.warning(f"admin notify failed: {e}")

    await msg.answer(
        render(get_settings()["welcome_text"], name=name),
        reply_markup=admin_kb() if is_admin else main_kb(),
    )
