"""Best-effort Telegram alerts for completed document packs.

Network and API errors are swallowed so a down Bot API never blocks PDF delivery.
Never include applicant PII in the message — only the referral tag.
"""

from __future__ import annotations

import requests

from app.config import get_settings
from app.services.pipeline import log

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_FIELD = 200


def _clip(value: str, limit: int = MAX_FIELD) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _html_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _display_ref(ref: str) -> str:
    key = (ref or "").strip().lower()
    if key in {"", "direct", "whatsapp", "wa"}:
        return "переход через WhatsApp"
    return _clip(ref) or "переход через WhatsApp"


def notify_generation(*, ref: str) -> None:
    """Send a generation notice with agency ref only. Safe for BackgroundTasks."""
    try:
        settings = get_settings()
        token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id
        if not token or not chat_id:
            log("Telegram: skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID empty)")
            return

        agency = _display_ref(ref)
        text = (
            "🚀 <b>Новая генерация пакета документов!</b>\n"
            f"• <b>Агентство (ref):</b> {_html_escape(agency)}"
        )
        response = requests.post(
            TELEGRAM_SEND_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=8,
        )
        if not response.ok:
            log(f"Telegram: send failed ({response.status_code})")
    except Exception as exc:
        log(f"Telegram: send error ({type(exc).__name__})")
