import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import ErrorEvent

from config import BOT_TOKEN, BOT_ID
from db import init_db
from handlers import register_all
from helpers import set_bot_username
from mongo_log import MongoErrorHandler
from settings import seed_from_json
from broadcast_worker import broadcast_worker, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# set as soon as Telegram says the token is invalid/revoked
token_dead = asyncio.Event()

TOKEN_HELP = (
    f"Telegram rejected the bot token (bot id {BOT_ID}): it was revoked, regenerated or is wrong. "
    "Fix: BotFather -> /mybots -> your bot -> API Token (or /revoke) -> copy the NEW token into "
    "BOT_TOKEN in .env (no quotes/spaces), also remove any old BOT_TOKEN variable from your "
    "hosting panel, then restart. Changing the database password does not affect this."
)


class GuardedBot(Bot):
    """Any API call that gets 401 Unauthorized flips `token_dead` so the app stops
    cleanly instead of retrying forever (aiogram would retry every ~5s without end)."""

    async def __call__(self, *args, **kwargs):
        try:
            return await super().__call__(*args, **kwargs)
        except TelegramUnauthorizedError:
            token_dead.set()
            raise


bot = GuardedBot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
register_all(dp)


@dp.error()
async def on_error(event: ErrorEvent):
    """Last line of defence: one bad update must never stop the bot."""
    logging.error(f"Unhandled error: {event.exception!r}", exc_info=event.exception)
    return True


async def main():
    init_db()
    seed_from_json()
    logging.getLogger().addHandler(MongoErrorHandler())   # warnings -> web panel
    logging.info("Bot starting...")

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        logging.critical(TOKEN_HELP)
        await bot.session.close()
        sys.exit(1)

    set_bot_username(me.username)
    logging.info(f"Bot: @{me.username} ({me.id})")

    tasks = [
        asyncio.create_task(broadcast_worker(bot)),
        asyncio.create_task(heartbeat(lambda: not token_dead.is_set())),
    ]
    polling = asyncio.create_task(dp.start_polling(bot))
    dead = asyncio.create_task(token_dead.wait())

    done, _ = await asyncio.wait({polling, dead}, return_when=asyncio.FIRST_COMPLETED)

    for t in tasks:
        t.cancel()
    if dead in done:
        logging.critical(TOKEN_HELP)
        polling.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await polling
        sys.exit(1)
    dead.cancel()
    await polling          # normal shutdown (Ctrl+C / SIGTERM)


if __name__ == "__main__":
    asyncio.run(main())
