import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from config import BOT_TOKEN
from db import init_db
from handlers import register_all
from helpers import set_bot_username
from mongo_log import MongoErrorHandler
from settings import seed_from_json
from broadcast_worker import broadcast_worker, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
    me = await bot.get_me()
    set_bot_username(me.username)
    logging.info(f"Bot: @{me.username} ({me.id})")
    asyncio.create_task(broadcast_worker(bot))
    asyncio.create_task(heartbeat())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
