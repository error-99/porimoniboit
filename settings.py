"""Live settings + tasks, edited from the web panel. Cached for a few seconds."""
import json
import logging
import time
from pathlib import Path

from db import settings_col, tasks_col

TTL = 5  # seconds — panel changes reach the bot within this time

DEFAULTS = {
    "mode": "random",              # random | sequential (inside the chosen category)
    "send_method": "forward",      # forward = original videos | album = real Telegram album (file_id)
    "album_enabled": True,
    "album_size": 3,               # 1..10
    "daily_limit": 5,              # 0 = unlimited
    "cooldown_seconds": 0,
    "bonus_limit": 5,              # extra videos after completing tasks
    "auto_delete_hours": 24,       # delete sent videos from the user's chat; 0 = never
    "share_button": True,          # the "Share Bot" button on the join screen
    "default_priority": 5,         # for categories you have not set
    "category_priority": [],       # [{"name": "funny", "p": 9}] ; p 0 = never send
    "caption_template": "",        # text under the video. {name} {left} {today} {total}
    "welcome_text": "👋 <b>স্বাগতম {name}!</b>\n\nনতুন ভিডিও পেতে নিচের বাটনে ক্লিক করুন।",
}

_s = {"t": 0.0, "v": None}
_t = {"t": 0.0, "v": []}


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def get_settings() -> dict:
    now = time.time()
    if _s["v"] is None or now - _s["t"] > TTL:
        try:
            doc = settings_col.find_one({"_id": "main"}) or {}
            s = {**DEFAULTS, **{k: v for k, v in doc.items() if k != "_id"}}
            s["album_size"] = _clamp(s["album_size"], 1, 10, 3)
            s["daily_limit"] = _clamp(s["daily_limit"], 0, 100000, 5)
            s["cooldown_seconds"] = _clamp(s["cooldown_seconds"], 0, 86400, 0)
            s["bonus_limit"] = _clamp(s["bonus_limit"], 0, 100000, 5)
            s["auto_delete_hours"] = _clamp(s["auto_delete_hours"], 0, 8760, 24)
            s["default_priority"] = _clamp(s["default_priority"], 0, 10, 5)
            cp = s["category_priority"]
            if isinstance(cp, list):
                cp = {x["name"]: _clamp(x.get("p"), 0, 10, s["default_priority"])
                      for x in cp if isinstance(x, dict) and "name" in x}
            s["category_priority"] = cp
            if s["send_method"] not in ("forward", "album"):
                s["send_method"] = "forward"
            if s["mode"] not in ("random", "sequential"):
                s["mode"] = "random"
            _s.update(t=now, v=s)
        except Exception as e:
            logging.warning(f"settings load failed, using last known: {e}")
            if _s["v"] is None:
                _s["v"] = dict(DEFAULTS)
            _s["t"] = now
    return _s["v"]


def get_tasks() -> list:
    now = time.time()
    if now - _t["t"] > TTL:
        try:
            _t["v"] = list(tasks_col.find({"enabled": True}).sort("order", 1))
        except Exception as e:
            logging.warning(f"tasks load failed, using last known: {e}")
        _t["t"] = now
    return _t["v"]


def seed_from_json():
    """First run only: import the old channels.json into the tasks collection."""
    f = Path(__file__).parent / "channels.json"
    try:
        if tasks_col.count_documents({}) == 0 and f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            for i, ch in enumerate(data.get("channels", [])):
                kind = (ch.get("type") or "channel").lower()
                tasks_col.insert_one({
                    "type": "group" if kind == "group" else "channel",
                    "title": ch.get("name", "Channel"),
                    "chat_id": str(ch.get("chat_id", "")),
                    "url": ch.get("url", ""),
                    "enabled": True, "order": i,
                })
            logging.info("Imported channels.json into tasks")
        settings_col.update_one(
            {"_id": "main"},
            {"$setOnInsert": {**DEFAULTS, "bonus_limit": int(
                json.loads(f.read_text(encoding="utf-8")).get("bonus_limit", 5)) if f.exists() else 5}},
            upsert=True,
        )
    except Exception as e:
        logging.warning(f"seed failed: {e}")
