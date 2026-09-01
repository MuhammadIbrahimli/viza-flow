"""Load environment variables and resolve project paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = ROOT / "temp"
TEMPLATES_DIR = ROOT / "templates"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
SCHENGEN_TEMPLATE = TEMPLATES_DIR / "schengen_italy.pdf"
DS160_TEMPLATE = TEMPLATES_DIR / "us_ds160_template.json"


def load_env_file(path: Path | None = None) -> None:
    """Read KEY=VALUE from project-root .env into os.environ (does not override existing vars)."""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _csv_origins(raw: str) -> list[str]:
    items = [item.strip() for item in (raw or "").split(",") if item.strip()]
    return items or ["*"]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    cors_origins: list[str]
    gemini_api_key: str
    gemini_model: str
    whatsapp_e164: str
    telegram_bot_token: str
    telegram_chat_id: str
    max_files: int
    max_file_bytes: int


def get_settings() -> Settings:
    load_env_file()
    return Settings(
        host=(os.environ.get("HOST") or "127.0.0.1").strip(),
        port=int(os.environ.get("PORT") or "8000"),
        cors_origins=_csv_origins(os.environ.get("CORS_ORIGINS") or "*"),
        gemini_api_key=(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip(),
        gemini_model=(os.environ.get("GEMINI_MODEL") or "").strip(),
        whatsapp_e164=(os.environ.get("WHATSAPP_E164") or "994557511233").strip(),
        telegram_bot_token=(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip(),
        telegram_chat_id=(os.environ.get("TELEGRAM_CHAT_ID") or "").strip(),
        max_files=int(os.environ.get("MAX_FILES") or "5"),
        max_file_bytes=int(os.environ.get("MAX_FILE_BYTES") or str(12 * 1024 * 1024)),
    )


def ensure_runtime_dirs() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
