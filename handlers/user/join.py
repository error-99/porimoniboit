import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from db import users_col
from helpers import today_str, get_not_joined, get_bonus_limit

router = Router()


async def _safe_answer(cb: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await cb.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        msg = str(e)
        if "query is too old" in msg or "query ID is invalid" in msg:
            return
        logging.warning(f"fj_check answer failed: {e}")
    except Exception as e:
        logging.warning(f"fj_check answer failed: {e}")


@router.callback_query(F.data == "fj_check")
async def on_fj_check(cb: CallbackQuery):
    # Answer immediately so Telegram doesn't expire the query
    await _safe_answer(cb, "⏳ Checking…")

    uid   = cb.from_user.id
    today = today_str()

    user = users_col.find_one({"_id": uid})
    if not user:
        await _safe_answer(cb, "আবার /start দিন", show_alert=True)
        return

    try:
        not_joined = await get_not_joined(cb.bot, uid)
    except Exception as e:
        logging.warning(f"get_not_joined failed: {e}")
        not_joined = []

    if not_joined:
        await _safe_answer(
            cb,
            f"❌ এখনো {len(not_joined)}টি চ্যানেল জয়েন করেননি!",
            show_alert=True,
        )
        return

    if user.get("stats", {}).get("bonus_date") != today:
        users_col.update_one(
            {"_id": uid},
            {"$set": {"stats.bonus_date": today}},
        )
        bonus = get_bonus_limit()
        try:
            await cb.message.edit_text(
                f"🎉 ধন্যবাদ! আপনি <b>+{bonus} ভিডিও</b> বোনাস পেয়েছেন।\n\n"
                f"এখন 🎬 <b>Get Video</b> ক্লিক করুন।",
            )
        except Exception as e:
            logging.info(f"fj_check edit_text: {e}")
    else:
        try:
            await cb.message.edit_text(
                "✅ সব চ্যানেল জয়েন করা আছে। Get Video চাপুন।"
            )
        except Exception as e:
            logging.info(f"fj_check edit_text: {e}")
