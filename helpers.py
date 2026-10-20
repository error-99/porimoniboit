import logging
from datetime import datetime, timezone
from urllib.parse import quote

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from settings import get_settings, get_tasks

GET_VIDEO_TEXT = "🎬 Get Video"
ADMIN_STATUS_TEXT = "📊 Status"
ADMIN_USERS_TEXT = "👥 Users"

BOT_USERNAME = ""


def set_bot_username(username: str):
    global BOT_USERNAME
    BOT_USERNAME = username or ""
    logging.info(f"Bot username cached: @{BOT_USERNAME}")


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, **kw) -> str:
    """Fill {name}/{left}/... without crashing on stray braces."""
    try:
        return (template or "").format_map(_Safe(**kw))
    except Exception:
        return template or ""


# ---------- Tasks (managed in the web panel) ----------
def get_force_channels() -> list:
    """Tasks the bot can verify (channel / group)."""
    return [t for t in get_tasks() if t.get("type") in ("channel", "group")]


def get_bonus_limit() -> int:
    return get_settings()["bonus_limit"]


# ---------- Time ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- Keyboards ----------
def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=GET_VIDEO_TEXT)]],
        resize_keyboard=True, is_persistent=False,
    )


def admin_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATUS_TEXT), KeyboardButton(text=ADMIN_USERS_TEXT)],
            [KeyboardButton(text=GET_VIDEO_TEXT)],
        ],
        resize_keyboard=True, is_persistent=False,
    )


async def get_not_joined(bot, user_id: int) -> list:
    """Channel/group tasks the user has not joined yet.

    If Telegram refuses the check (bot not admin there, wrong chat_id) the task is
    skipped — otherwise every user would be stuck forever — and the problem is
    written to the Errors page of the web panel."""
    not_joined = []
    for t in get_force_channels():
        try:
            member = await bot.get_chat_member(t["chat_id"], user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(t)
        except Exception as e:
            logging.warning(f"Task check failed for '{t.get('title')}' ({t.get('chat_id')}): {e}")
    return not_joined


def force_join_kb(not_joined: list) -> InlineKeyboardMarkup:
    rows = []
    for t in not_joined:
        group = t.get("type") == "group"
        rows.append([InlineKeyboardButton(
            text=f"{'👥 Join Group' if group else '📢 Join Channel'}: {t['title']}",
            url=t["url"],
        )])

    # custom links are shown but can't be verified by Telegram
    for t in get_tasks():
        if t.get("type") == "link" and t.get("url"):
            rows.append([InlineKeyboardButton(text=f"🔗 {t['title']}", url=t["url"])])

    if BOT_USERNAME and get_settings()["share_button"]:
        share_url = (
            f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}"
            f"&text={quote('🔥 Best video bot! Get fresh videos:')}"
        )
        rows.append([InlineKeyboardButton(text="🚀 Share Bot (Required)", url=share_url)])

    rows.append([InlineKeyboardButton(text="✅ I Joined", callback_data="fj_check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
