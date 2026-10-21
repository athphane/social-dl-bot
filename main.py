#!/usr/bin/env python3
"""Run the Social Downloader Bot over Telegram's MTProto API."""

import logging
from configparser import ConfigParser
from pathlib import Path

from pyrogram import Client

from bot.handlers import register_handlers

LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.ini"


def build_client(config_path=CONFIG_PATH):
    """Validate credentials and prepare a client without connecting to Telegram."""
    config_path = Path(config_path)
    config = ConfigParser(interpolation=None)
    if not config.read(config_path):
        raise ValueError(f"Config file not found: {config_path}")

    try:
        api_id = config.getint("pyrogram", "api_id", fallback=0)
    except ValueError:
        raise ValueError("api_id in [pyrogram] must be a positive integer") from None
    api_hash = config.get("pyrogram", "api_hash", fallback="").strip()
    bot_token = config.get("pyrogram", "bot_token", fallback="").strip()
    if api_id <= 0 or not api_hash or not bot_token:
        raise ValueError(
            "Set api_id, api_hash and bot_token in config.ini [pyrogram]. "
            "Get API credentials from https://my.telegram.org/apps."
        )

    workdir = Path(config.get("pyrogram", "workdir", fallback="workdir"))
    if not workdir.is_absolute():
        workdir = config_path.resolve().parent / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    socks5_proxy = config.get("proxy", "socks5_proxy", fallback="").strip() or None

    client = Client(
        "social_dl_bot",
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        workdir=str(workdir),
        workers=1,
    )
    register_handlers(client, socks5_proxy=socks5_proxy)
    return client


def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
        ],
    )
    try:
        client = build_client()
    except ValueError as error:
        LOGGER.error("%s", error)
        return 1

    LOGGER.info("Social Downloader Bot starting over MTProto. Press Ctrl+C to stop.")
    client.run()
    LOGGER.info("Bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
