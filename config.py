"""Secrets and connection settings. Everything comes from .env — no defaults here."""
import os
from pathlib import Path
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return v


BOT_TOKEN = _req("BOT_TOKEN")
ADMIN_ID = int(_req("ADMIN_ID"))

if os.getenv("MONGO_URI"):
    MONGO_URI = os.environ["MONGO_URI"]
else:
    MONGO_URI = (
        f"mongodb://{quote_plus(_req('MONGO_USER'))}:{quote_plus(_req('MONGO_PASS'))}"
        f"@{_req('MONGO_HOST')}:{int(os.getenv('MONGO_PORT', '27017'))}/{_req('MONGO_DB')}"
        f"?authSource={os.getenv('MONGO_AUTH_SOURCE') or _req('MONGO_DB')}"
    )
MONGO_DB = _req("MONGO_DB")

# Chat where the bot forwards each video ONCE to learn its file_id (the copy is
# deleted right away). Must be a chat the bot can write to — your own private
# chat with the bot (press /start first) or a private channel where it is admin.
DUMP_CHAT_ID = int(os.getenv("DUMP_CHAT_ID") or ADMIN_ID)

# Delete the temporary forwarded copy in DUMP_CHAT_ID. Set DELETE_DUMP=0 in .env to keep it.
DELETE_DUMP = os.getenv("DELETE_DUMP", "1") not in ("0", "false", "False", "no")
