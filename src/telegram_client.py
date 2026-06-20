"""Telegram Publisher — sends messages and the chart photo to a target chat.

Outbound only (sendMessage / sendPhoto), so it never conflicts with any other bot
that polls updates on a different token. Includes rate-limit aware retry/backoff and
a getUpdates helper used once on deploy to discover the group chat IDs.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import config

log = logging.getLogger("telegram")

API = "https://api.telegram.org"
MSG_LIMIT = 4096
CAPTION_LIMIT = 1024

_SESSION = requests.Session()


class TelegramTransient(Exception):
    pass


class TelegramError(Exception):
    pass


def _url(method: str) -> str:
    return f"{API}/bot{config.telegram_token}/{method}"


@retry(
    retry=retry_if_exception_type((requests.RequestException, TelegramTransient)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _post(method: str, data=None, files=None) -> dict:
    if not config.telegram_token:
        raise TelegramError("TELEGRAM_BOT_TOKEN not configured")
    resp = _SESSION.post(_url(method), data=data, files=files, timeout=40)
    if resp.status_code == 429:
        retry_after = 1
        try:
            retry_after = int(resp.json().get("parameters", {}).get("retry_after", 1))
        except Exception:
            pass
        log.warning("Telegram 429 — sleeping %ss", retry_after)
        time.sleep(min(retry_after, 30))
        raise TelegramTransient("rate limited")
    if resp.status_code >= 500:
        raise TelegramTransient(f"server error {resp.status_code}")
    payload = resp.json()
    if not payload.get("ok"):
        raise TelegramError(f"Telegram API error: {payload.get('description')}")
    return payload["result"]


def _split(text: str, limit: int = MSG_LIMIT) -> List[str]:
    """Split a long message on line boundaries to respect Telegram's limit."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current.rstrip("\n"))
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip("\n"))
    return chunks


def send_message(text: str, chat_id: Optional[str] = None) -> List[int]:
    """Send a (possibly long) text message. Returns the message_id(s)."""
    chat_id = chat_id or config.target_chat_id
    if not chat_id:
        raise TelegramError("No chat_id configured for the current publish target")
    ids = []
    for chunk in _split(text):
        result = _post("sendMessage", data={
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        })
        ids.append(result["message_id"])
    log.info("Sent %d message part(s) to %s", len(ids), chat_id)
    return ids


def send_photo(photo_path: str, caption: str = "", chat_id: Optional[str] = None) -> int:
    """Send the chart image with an optional caption.

    If the caption exceeds Telegram's caption limit, the photo is sent first and the
    full caption follows as a separate message (so nothing is truncated).
    """
    chat_id = chat_id or config.target_chat_id
    if not chat_id:
        raise TelegramError("No chat_id configured for the current publish target")

    long_caption = len(caption) > CAPTION_LIMIT
    photo_caption = "" if long_caption else caption
    with open(photo_path, "rb") as fh:
        result = _post(
            "sendPhoto",
            data={"chat_id": chat_id, "caption": photo_caption},
            files={"photo": fh},
        )
    msg_id = result["message_id"]
    if long_caption:
        send_message(caption, chat_id=chat_id)
    log.info("Sent photo to %s (caption %s)", chat_id, "separate" if long_caption else "inline")
    return msg_id


def get_updates() -> list:
    """Fetch recent updates — used once on deploy to discover group chat IDs."""
    return _post("getUpdates", data={"timeout": 0})


def discover_chat_ids() -> dict:
    """Return {title: chat_id} for groups the bot can currently see in getUpdates."""
    seen = {}
    for upd in get_updates():
        for key in ("message", "my_chat_member", "channel_post"):
            chat = (upd.get(key) or {}).get("chat")
            if chat and chat.get("type") in ("group", "supergroup", "channel"):
                seen[chat.get("title", str(chat["id"]))] = chat["id"]
    return seen
