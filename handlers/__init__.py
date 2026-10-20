from aiogram import Dispatcher

from .user import start as user_start
from .user import get_video as user_get_video
from .user import join as user_join

from .admin import status as admin_status
from .admin import users as admin_users
from .admin import delete as admin_delete
from .admin import tools as admin_tools      # <-- NEW


def register_all(dp: Dispatcher):
    dp.include_router(admin_status.router)
    dp.include_router(admin_users.router)
    dp.include_router(admin_delete.router)
    dp.include_router(admin_tools.router)     # <-- NEW (before user routers)

    dp.include_router(user_start.router)
    dp.include_router(user_join.router)
    dp.include_router(user_get_video.router)
