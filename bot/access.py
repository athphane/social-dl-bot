"""
Access control module for Social Downloader Bot.

Manages an allowlist of Telegram user IDs who are permitted to use
the bot via private chat (DM). Users who are members of the allowed
group get automatic access.

The allowlist is persisted as a JSON file alongside config.ini.
"""

import json
import logging
import os

from pyrogram.enums import ChatMemberStatus

LOGGER = logging.getLogger(__name__)

# Path to the allowlist file (next to config.ini, outside the bot/ package)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST_PATH = os.path.join(BASE_DIR, "allowlist.json")

# Default values — will be set by the caller
OWNER_ID = None
ALLOWED_GROUP = None


def _load_allowlist() -> set[int]:
    """Load the allowlist from disk. Returns an empty set if missing/corrupt."""
    if not os.path.exists(ALLOWED_PATH := ALLOWLIST_PATH):
        return set()
    try:
        with open(ALLOWED_PATH, "r") as f:
            data = json.load(f)
        return set(data.get("allowed_users", []))
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("Failed to load allowlist, starting fresh: %s", e)
        return set()


def _save_allowlist(users: set[int]) -> None:
    """Persist the allowlist to disk."""
    try:
        with open(ALLOWLIST_PATH, "w") as f:
            json.dump({"allowed_users": sorted(users)}, f, indent=2)
    except OSError as e:
        LOGGER.error("Failed to save allowlist: %s", e)


def add_user(user_id: int) -> bool:
    """Add a user to the allowlist. Returns True if newly added."""
    users = _load_allowlist()
    if user_id in users:
        return False
    users.add(user_id)
    _save_allowlist(users)
    LOGGER.info("Added user %s to allowlist", user_id)
    return True


def remove_user(user_id: int) -> bool:
    """Remove a user from the allowlist. Returns True if they were present."""
    users = _load_allowlist()
    if user_id not in users:
        return False
    users.discard(user_id)
    _save_allowlist(users)
    LOGGER.info("Removed user %s from allowlist", user_id)
    return True


def list_users() -> list[int]:
    """Return the sorted list of allowed user IDs."""
    return sorted(_load_allowlist())


async def is_user_allowed(user_id: int, bot=None) -> bool:
    """
    Check whether a user is allowed to use the bot via DM.

    Allowed if:
      1. User is the bot owner
      2. User is in the allowlist
      3. User is a member of the allowed group (checked via MTProto)

    The bot parameter is required for group-membership checking.
    """
    # Owner always has access
    if OWNER_ID and user_id == OWNER_ID:
        return True

    # Check local allowlist
    allowlist = _load_allowlist()
    if user_id in allowlist:
        return True

    # Check group membership (if we have a bot instance)
    if bot and ALLOWED_GROUP:
        try:
            member = await bot.get_chat_member(chat_id=ALLOWED_GROUP, user_id=user_id)
            # Member statuses that count as "in the group"
            if member.status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ) or (member.status == ChatMemberStatus.RESTRICTED and member.is_member):
                LOGGER.info(
                    "User %s is a member of the allowed group — granting DM access",
                    user_id,
                )
                return True
        except Exception as e:
            LOGGER.debug(
                "Could not check group membership for user %s: %s", user_id, e
            )

    return False
