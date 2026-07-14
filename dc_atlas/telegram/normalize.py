"""
Telegram source URL normalization for DC Atlas.

Converts various Telegram URL formats to canonical usernames.
Ensures unique storage keys and rejects private invites.
"""

import re

# Valid Telegram username pattern (relaxed for normalization)
# Telegram officially requires 5-32 chars, but we accept 2+ locally
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{1,31}[a-zA-Z0-9]$")


def normalize_telegram_source(raw: str) -> str:
    """
    Normalize a Telegram URL/username to a lowercase canonical key.

    Returns the canonical username string or an error string starting with 'error_'.
    """
    if not raw or not raw.strip():
        return "error_empty"

    text = raw.strip()

    # Remove protocol and domain
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^(?:t\.me|telegram\.me)/", "", text)
    text = re.sub(r"^www\.", "", text)

    # Remove leading @
    text = re.sub(r"^@", "", text)

    # Remove trailing slash
    text = text.rstrip("/")

    # Reject private invites
    if text.startswith("+"):
        return "error_private_or_invite_not_supported"
    if text.startswith("joinchat/"):
        return "error_private_or_invite_not_supported"
    if "/joinchat/" in text:
        return "error_private_or_invite_not_supported"
    if "+" in text and not text.startswith("+"):
        return "error_private_or_invite_not_supported"

    # Strip post ID (/123)
    text = re.sub(r"/\d+$", "", text)

    # Lowercase for key
    canonical = text.lower()

    # Validate format
    if not _USERNAME_PATTERN.match(canonical):
        return "error_invalid_username"

    return canonical
