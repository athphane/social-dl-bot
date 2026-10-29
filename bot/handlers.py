"""
Telegram message handlers for the Social Downloader Bot.

Listens in groups for TikTok, Instagram, and YouTube Shorts links
and auto-downloads/uploads them. Also supports DM with access control.
"""

import asyncio
import logging
import os
import re
import shutil

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.errors import BadRequest
from pyrogram.handlers import MessageHandler
from pyrogram.types import InputMediaPhoto, Message

from bot.access import (
    add_user,
    is_user_allowed,
    list_users,
    remove_user,
)
from bot.downloader import (
    AUTO_DOWNLOAD_REGEX,
    ALL_PLATFORMS_REGEX,
    detect_platform,
    download_video,
)
from bot.limits import MAX_VIDEO_SIZE

LOGGER = logging.getLogger(__name__)

# Non-premium MTProto upload limit enforced by Kurigram.
MAX_FILE_SIZE = MAX_VIDEO_SIZE


def register_handlers(app: Client, socks5_proxy=None):
    """Register MTProto message handlers on the client."""

    # ── Configuration ──────────────────────────────────────────────
    OWNER_ID = 352665135
    IGNORED_USERS = {394234655}
    ALLOWED_GROUP = -1001837077491

    # ═══════════════════════════════════════════════════════════════
    #  ACCESS CONTROL — Initialise the allowlist module
    #  (set the shared globals so is_user_allowed works)
    # ═══════════════════════════════════════════════════════════════
    # noinspection PyProtectedMember
    import bot.access as _ac
    _ac.OWNER_ID = OWNER_ID
    _ac.ALLOWED_GROUP = ALLOWED_GROUP

    IS_OWNER = filters.user(OWNER_ID)

    # ═══════════════════════════════════════════════════════════════
    #  HANDLERS
    # ═══════════════════════════════════════════════════════════════

    async def auto_download(client: Client, message: Message):
        """
        Auto-download TikTok, Instagram Reels, YouTube Shorts links
        posted by ANYONE in the allowed group (except ignored users).
        """
        if not message or not message.text:
            return

        # Skip owner's and ignored users' messages
        if message.from_user and message.from_user.id in {OWNER_ID} | IGNORED_USERS:
            return

        text = message.text.strip()
        platform, match = detect_platform(text)
        if not platform:
            return

        original_url = match.group(0)

        # Only auto-download TikTok, Instagram, YouTube Shorts
        if not any(re.search(p, text, re.IGNORECASE) for p in [
            r'https?://(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com)/(?:p|reels|reel|tv|stories|share/reel|share/p)/[a-zA-Z0-9_-]+/?',
            r'https?://(?:www\.|vm\.|vt\.|t\.)?tiktok\.com/(?:@[\w.-]*/video/\d+|@/video/\d+|[\w]+/?).*',
            r'https?://(?:www\.)?youtube\.com/shorts/[a-zA-Z0-9_-]+/?(?:\?.*)?',
        ]):
            return

        await process_and_upload(message, original_url, socks5_proxy)

    async def dl_command(client: Client, message: Message):
        """
        /dl <url> — manually download any supported video.
        Works in groups AND DM (if user is allowed).
        """
        if not message or not message.text:
            return

        if message.chat.type == ChatType.PRIVATE and not await is_user_allowed(
            message.from_user.id, bot=client
        ):
            await message.reply_text("❌ Sorry, you're not authorised to use this bot in private chat.")
            return

        args = message.command[1:]
        if not args:
            await message.reply_text(
                "Usage: `/dl <url>`\nExample: `/dl https://youtu.be/xxxxx`",
            )
            return

        url = " ".join(args)

        if not re.search(ALL_PLATFORMS_REGEX, url, re.IGNORECASE):
            await message.reply_text(
                "That doesn't look like a supported video URL. "
                "Supported: TikTok, Instagram, YouTube, YouTube Shorts, Facebook."
            )
            return

        await process_and_upload(message, url, socks5_proxy)

    async def start_command(client: Client, message: Message):
        """Welcome message."""
        await message.reply_text(
            "🤖 **Social Downloader Bot**\n\n"
            "I auto-download videos from these platforms when links are shared:\n"
            "• **TikTok** — tiktok.com\n"
            "• **Instagram Reels** — instagram.com/reel/...\n"
            "• **YouTube Shorts** — youtube.com/shorts/...\n\n"
            "Just send a link in the chat and I'll handle the rest! 🚀\n\n"
            "**Manual command:** `/dl <url>` — works for YouTube, Facebook too.\n\n"
            "💬 **DM access:** If you're a member of the group, you can DM me links too!"
        )

    async def help_command(client: Client, message: Message):
        """Help message."""
        await message.reply_text(
            "**How to use this bot:**\n\n"
            "🔹 **Auto-download** — Just paste a link to TikTok, Instagram Reel, "
            "or YouTube Shorts in the group chat. I'll download and upload it.\n\n"
            "🔹 **Manual download** — Use `/dl <url>` for YouTube videos "
            "or Facebook videos.\n\n"
            "🔹 **DM me links** — If you're a member of the group, you can also "
            "send me links in private chat and I'll download them for you.\n\n"
            "**Supported platforms:**\n"
            "✅ TikTok\n"
            "✅ Instagram (Reels, Posts, Stories)\n"
            "✅ YouTube Shorts\n"
            "✅ YouTube (via `/dl`)\n"
            "✅ Facebook (via `/dl`)\n\n"
            "Just add me to your group and give me message permissions!"
        )

    # ═══════════════════════════════════════════════════════════════
    #  ACCESS CONTROL COMMANDS (Owner only)
    # ═══════════════════════════════════════════════════════════════

    async def allow_command(client: Client, message: Message):
        """
        /allow <user_id> — Add a user to the DM allowlist.
        Owner only.
        """
        if not message:
            return

        args = message.command[1:]
        if not args:
            allowed = list_users()
            if allowed:
                msg = "**Allowed users:**\n" + "\n".join(
                    f"• `{uid}`" for uid in allowed
                )
            else:
                msg = "No users in the allowlist yet."
            await message.reply_text(msg)
            return

        try:
            user_id = int(args[0])
        except ValueError:
            await message.reply_text(
                "❌ Invalid user ID. Use: `/allow <user_id>`"
            )
            return

        if user_id == OWNER_ID:
            await message.reply_text("🤦 You're the owner. You're always allowed.")
            return

        if add_user(user_id):
            await message.reply_text(f"✅ User `{user_id}` has been allowed to use the bot via DM.")
        else:
            await message.reply_text(f"ℹ️ User `{user_id}` is already in the allowlist.")

    async def disallow_command(client: Client, message: Message):
        """
        /disallow <user_id> — Remove a user from the DM allowlist.
        Owner only.
        """
        if not message:
            return

        args = message.command[1:]
        if not args:
            await message.reply_text(
                "Usage: `/disallow <user_id>`\n\n"
                "Use `/allowlist` to see all allowed users."
            )
            return

        try:
            user_id = int(args[0])
        except ValueError:
            await message.reply_text(
                "❌ Invalid user ID. Use: `/disallow <user_id>`"
            )
            return

        if user_id == OWNER_ID:
            await message.reply_text("🤦 You can't disallow yourself, boss.")
            return

        if remove_user(user_id):
            await message.reply_text(f"✅ User `{user_id}` has been removed from the allowlist.")
        else:
            await message.reply_text(f"ℹ️ User `{user_id}` was not in the allowlist.")

    async def allowlist_command(client: Client, message: Message):
        """List all users in the DM allowlist."""
        allowed = list_users()
        if allowed:
            msg = "**📋 DM Allowlist:**\n\n" + "\n".join(
                f"• `{uid}`" for uid in allowed
            )
        else:
            msg = "📋 The allowlist is empty. Use `/allow <user_id>` to add someone."
        await message.reply_text(msg)

    # ═══════════════════════════════════════════════════════════════
    #  DM HANDLER
    # ═══════════════════════════════════════════════════════════════

    async def dm_message_handler(client: Client, message: Message):
        """
        Handle messages sent directly to the bot in private chat.
        Checks access control before processing.
        """
        if not message or not message.text:
            return

        user = message.from_user
        if not user:
            return

        user_id = user.id
        text = message.text.strip()

        # Check access
        if not await is_user_allowed(user_id, bot=client):
            await message.reply_text(
                "❌ Sorry, you're not authorised to use this bot in private chat.\n\n"
                "Join the group and try again, or ask the group admin to add you to the allowlist."
            )
            return

        # Skip commands — they're handled separately
        if text.startswith("/"):
            return

        # Check if it's a supported link
        platform, match = detect_platform(text)
        if not platform:
            await message.reply_text(
                "Send me a link to a TikTok, Instagram, YouTube Shorts, "
                "or YouTube/Facebook video!\n\n"
                "Or use `/dl <url>` for any supported platform."
            )
            return

        original_url = match.group(0)
        await process_and_upload(message, original_url, socks5_proxy)

    # Kurigram runs the first matching handler in a group.
    incoming_text = filters.incoming & filters.text
    for command, callback in (
        ("start", start_command), ("help", help_command), ("dl", dl_command),
    ):
        app.add_handler(MessageHandler(callback, incoming_text & filters.command(command)))

    for command, callback in (
        ("allow", allow_command), ("disallow", disallow_command),
        ("allowlist", allowlist_command),
    ):
        app.add_handler(MessageHandler(
            callback, incoming_text & filters.command(command) & IS_OWNER,
        ))

    non_command_text = incoming_text & ~filters.regex(r"^/")
    app.add_handler(MessageHandler(
        dm_message_handler, non_command_text & filters.private,
    ))
    app.add_handler(MessageHandler(
        auto_download,
        non_command_text & filters.chat(ALLOWED_GROUP)
        & filters.regex(AUTO_DOWNLOAD_REGEX, re.IGNORECASE),
    ))

    LOGGER.info("All handlers registered. DM access control active!")
    LOGGER.info("Owner ID: %s | Allowed Group: %s", OWNER_ID, ALLOWED_GROUP)


async def process_and_upload(message: Message, url: str, socks5_proxy=None):
    """
    Download a video and upload it to the chat.
    Shows a status message so the group knows what's happening.
    """
    platform, _ = detect_platform(url)
    if not platform:
        return

    # Status message
    status_msg = await message.reply_text(
        f"⬇️ Downloading {platform} media...",
        disable_notification=True,
    )

    result = None
    try:
        result = await download_video(url, socks5_proxy)

        if not result["success"]:
            await status_msg.edit_text(f"❌ Download failed: {result['error']}")
            await asyncio.sleep(10)
            await status_msg.delete()
            return

        file_path = result["file_path"]
        media_type = result.get("media_type", "video")
        file_size = os.path.getsize(file_path)

        if file_size > MAX_FILE_SIZE and result.get("fallback_image_path"):
            file_path = result["fallback_image_path"]
            media_type = "photo"
            file_size = os.path.getsize(file_path)

        if file_size > MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"⚠️ Media is too large ({file_size / 1024 / 1024:.1f} MiB). "
                "The MTProto upload limit is 2000 MiB."
            )
            await asyncio.sleep(10)
            await status_msg.delete()
            return

        # Update status
        await status_msg.edit_text(f"⬆️ Uploading {media_type}...")

        # Build caption
        caption = f"{result['title']}\n{result['download_url']}"[:1024]

        if media_type == "photos":
            await send_photos(message, result["file_paths"], caption)
        elif media_type == "photo":
            await send_photo(message, file_path, caption)
        else:
            try:
                await message.reply_video(
                    file_path, caption=caption, supports_streaming=True,
                    parse_mode=ParseMode.DISABLED,
                )
            except Exception:
                if not result.get("fallback_image_path"):
                    raise
                LOGGER.info("Rendered video upload failed; sending photo", exc_info=True)
                await send_photo(message, result["fallback_image_path"], caption)

        # Delete status
        await status_msg.delete()

    except Exception as e:
        LOGGER.exception("Error during download/upload")
        try:
            await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        # Clean up temp dir
        if result and result.get("temp_dir"):
            try:
                shutil.rmtree(result["temp_dir"], ignore_errors=True)
            except Exception:
                pass


async def send_photos(message, file_paths, caption):
    """Send ordered albums of up to ten photos, with one caption per post."""
    for start in range(0, len(file_paths), 10):
        batch = file_paths[start:start + 10]
        batch_caption = caption if start == 0 else ""
        if len(batch) == 1:
            await send_photo(message, batch[0], batch_caption)
            continue
        try:
            await message.reply_media_group([
                InputMediaPhoto(path, caption=batch_caption if index == 0 else "",
                                parse_mode=ParseMode.DISABLED)
                for index, path in enumerate(batch)
            ])
        except BadRequest:
            # Preserve the existing document fallback for rejected images.
            for index, path in enumerate(batch):
                await send_photo(message, path, batch_caption if index == 0 else "")


async def send_photo(message, file_path, caption):
    """Send as a photo, or as the original image file if Telegram rejects it."""
    try:
        await message.reply_photo(
            file_path, caption=caption, parse_mode=ParseMode.DISABLED,
        )
    except BadRequest:
        await message.reply_document(
            file_path, caption=caption, parse_mode=ParseMode.DISABLED,
        )
