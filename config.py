"""Secrets and connection settings. Everything comes from .env — no defaults here."""
import os
import re
from pathlib import Path
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
    # override=True: the .env file wins over stale BOT_TOKEN / MONGO_* variables that may
    # still be set in the hosting panel or shell (a common cause of "I changed it but nothing changed")
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass


def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return v


BOT_TOKEN = _req("BOT_TOKEN").strip().strip("\"'").strip()
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", BOT_TOKEN):
    raise RuntimeError(
        "BOT_TOKEN in .env does not look like a Telegram token (expected 123456789:AAAA...). "
        "Check for extra spaces, quotes or a half-copied value.")
BOT_ID = int(BOT_TOKEN.split(":", 1)[0])
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
