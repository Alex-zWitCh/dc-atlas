"""
Real Delta Chat adapter using deltachat-rpc-client.

Uses the official Bot/Client API which properly handles ALL events:
SecureJoin handshakes, incoming messages, contact requests, etc.

Requires:
  - deltachat-rpc-server binary in PATH
  - pip install deltachat-rpc-client
"""

import logging
import queue as _queue
import threading
from pathlib import Path
from typing import Callable, Optional

from deltachat_rpc_client import Rpc, DeltaChat
from deltachat_rpc_client.client import Bot
from deltachat_rpc_client.const import EventType
from deltachat_rpc_client.events import EventFilter, NewMessage, RawEvent

logger = logging.getLogger(__name__)


def _format_bytes(n: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class ChannelRef:
    """Reference to a Delta Chat broadcast list (Channel)."""

    def __init__(self, channel_id: int, chat, title: str):
        self.id = channel_id
        self._chat = chat
        self.title = title


class DeltaChatAdapter:
    """
    Adapter wrapping the official deltachat-rpc-client Bot API.

    - Runs Bot event loop in a background daemon thread
    - Places incoming messages in a thread-safe queue
    - Provides DC Atlas compatible interface
    - SecureJoin is handled by the DC core automatically
    """

    def __init__(self, profile_dir: str = ""):
        self._profile_dir = profile_dir or str(
            Path.home() / ".config" / "dc-atlas"
        )
        self._rpc = None
        self._account = None
        self._bot = None
        self._msg_queue = _queue.Queue()
        self._started = False
        self._stop_event = threading.Event()

    # --- Lifecycle ---

    def configure(
        self,
        email: str,
        password: str,
        display_name: str = "DC Atlas",
        imap_server: str = "",
        smtp_server: str = "",
    ) -> None:
        """Configure and log in to a Delta Chat account."""
        logger.info("Configuring DC account: %s", email)

        self._rpc = Rpc(accounts_dir=self._profile_dir)
        self._rpc.start()

        self._dc = DeltaChat(self._rpc)
        # Reuse existing unconfigured account, or create new one
        accounts = self._dc.get_all_accounts()
        self._account = accounts[0] if accounts else self._dc.add_account()

        # Set display name and bot mode
        self._account.set_config("displayname", display_name)
        self._account.set_config("bot", "1")
        # Enable DC core debug logging for diagnostics
        self._account.set_config("debug_logging", "1")

        # Configure transport with IMAP/SMTP settings
        transport_params = {"addr": email, "password": password}
        if imap_server:
            transport_params["imap_server"] = imap_server
            transport_params["imap_port"] = "993"
        if smtp_server:
            transport_params["smtp_server"] = smtp_server
            transport_params["smtp_port"] = "465"
        self._account.add_or_update_transport(transport_params)

        # Configure account
        self._account.configure()
        self._account.start_io()
        self._started = True
        logger.info("DC account configured and online")

    def _imap_poll(self) -> None:
        """
        Force the DC core to reconnect to IMAP and scan for new messages.
        Returns True if reconnected, False otherwise.
        """
        if not self._account:
            return False
        try:
            self._account.stop_io()
            self._account.start_io()
            logger.debug("IMAP reconnected for inbox scan")
            return True
        except Exception as e:
            logger.debug("IMAP reconnect error: %s", e)
            return False

    def start(self) -> None:
        """Start the bot event loop in a background thread."""
        if self._bot:
            return

        if not self._account:
            raise RuntimeError("Adapter not configured, call configure() first")

        # Create Bot with hooks for incoming messages and raw events
        self._bot = Bot(
            self._account,
            hooks=[
                (self._on_new_message, NewMessage()),
                (self._on_raw_event, RawEvent()),
            ],
        )

        # Start event processing in a daemon thread
        self._stop_event.clear()
        t = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="dc-event-loop",
        )
        t.start()
        logger.debug("DC event loop started in background thread")

        # Send an Autocrypt-gossip message to self to publish
        # the bot's public key with relay info. Without this, SecureJoin
        # fails because no client can discover the bot's Autocrypt key.
        try:
            self_contact = self._account.self_contact
            self_addr = self_contact.get_snapshot().address
            # Create a 1:1 chat with self and send a message — this triggers
            # Autocrypt gossip which publishes the bot's key + relay info.
            chat = self._account.create_chat_by_contact_id(self_contact.id)
            chat.send_text("init")
            logger.debug("Sent self-message to publish Autocrypt key")
        except Exception as e:
            logger.debug("Self-contact init: %s", e)

    def _run_event_loop(self) -> None:
        """Process DC events in a loop. Blocks on wait_for_event()."""
        try:
            self._bot.run_until(lambda e: self._stop_event.is_set())
        except Exception as e:
            logger.error("Event loop stopped: %s", e)
        logger.debug("DC event loop ended")

    def _on_new_message(self, event) -> None:
        """Hook called by Bot on each INCOMING_MSG event."""
        try:
            snapshot = event.message_snapshot
            # Skip system/info messages (from_id=2=SpecialContactId.INFO)
            # and messages from self (from_id=1=SpecialContactId.SELF)
            if snapshot.from_id in (1, 2):
                # For info messages (from_id=2) — SecureJoin just created a
                # new 1:1 chat. Try to extract the new contact from the chat.
                if snapshot.from_id == 2 and snapshot.chat_id:
                    try:
                        chat = self._account.get_chat_by_id(snapshot.chat_id)
                        chat_snap = chat.get_basic_snapshot()
                        # Only handle 1:1 chats (chat_type 100)
                        if chat_snap.chat_type in (100, "Single", "single"):
                            # Get the other contact from the chat
                            contacts = chat.get_contacts()
                            for c in contacts:
                                c_snap = c.get_snapshot()
                                # Skip self-contact and system contacts
                                if c_snap.id not in (1, 2):
                                    sender_addr = c_snap.address
                                    msg = {
                                        "message_id": snapshot.id,
                                        "text": "",
                                        "sender_id": str(c_snap.id),
                                        "sender_addr": sender_addr,
                                        "chat_id": snapshot.chat_id,
                                        "is_new_contact": True,
                                        "is_direct_chat": True,
                                    }
                                    self._msg_queue.put(msg)
                                    break
                    except Exception:
                        pass
                return
            # Skip messages without text
            if not (snapshot.text or "").strip():
                return
            # Accept contact request chat
            try:
                chat = self._account.get_chat_by_id(snapshot.chat_id)
                chat.accept()
                # Determine if this is a 1:1 direct chat or group
                is_direct = False
                try:
                    chat_snap = chat.get_basic_snapshot()
                    chat_type = chat_snap.chat_type
                    is_direct = chat_type in (100, "Single", "single")
                    logger.info("Message chat_id=%s type=%s direct=%s from=%s", snapshot.chat_id, chat_type, is_direct, snapshot.from_id)
                except Exception as e:
                    logger.info("Could not get chat_type for chat %s: %s", snapshot.chat_id, e)
                    is_direct = True
                    is_direct = True
            except Exception:
                is_direct = True
            # Extract sender info
            sender = self._account.get_contact_by_id(snapshot.from_id)
            sender_addr = getattr(sender.get_snapshot(), "address", "")
            msg = {
                "message_id": snapshot.id,
                "text": snapshot.text,
                "sender_id": str(snapshot.from_id),
                "sender_addr": sender_addr,
                "chat_id": snapshot.chat_id,
                "is_direct_chat": is_direct,
            }
            self._msg_queue.put(msg)
        except Exception as e:
            logger.debug("on_new_message: %s", e)

    def _on_raw_event(self, event) -> None:
        """Hook called by Bot on ALL raw events — detect SecureJoin completions."""
        try:
            kind = event.get("kind", "")
            chat_id = event.get("chat_id", event.get("id", ""))
            # SecureJoin events — new contact joined via invite
            if kind in (EventType.SECUREJOIN_INVITER_PROGRESS, EventType.SECUREJOIN_JOINER_PROGRESS,
                        EventType.CONTACTS_CHANGED, EventType.CHAT_MODIFIED):
                logger.info("Raw event: %s chat_id=%s", kind, chat_id)
                if chat_id:
                    self._check_new_contact(chat_id)
        except Exception as e:
            logger.debug("on_raw_event: %s", e)
        except Exception as e:
            logger.debug("on_raw_event: %s", e)

    def _check_new_contact(self, chat_id) -> None:
        """If chat_id belongs to a new 1:1 contact, queue a welcome."""
        try:
            chat = self._account.get_chat_by_id(chat_id)
            snap = chat.get_basic_snapshot()
            if snap.chat_type in (100, "Single", "single"):
                contacts = chat.get_contacts()
                for c in contacts:
                    cs = c.get_snapshot()
                    if cs.id not in (1, 2):
                        msg = {
                            "message_id": 0,
                            "text": "",
                            "sender_id": str(cs.id),
                            "sender_addr": cs.address,
                            "chat_id": chat_id,
                            "is_new_contact": True,
                            "is_direct_chat": True,
                        }
                        self._msg_queue.put(msg)
                        logger.info("New contact detected via raw event: %s", cs.address)
                        break
        except Exception as e:
            logger.debug("_check_new_contact: %s", e)

    def stop(self) -> None:
        """Stop the bot event loop and close RPC."""
        self._stop_event.set()
        if self._bot:
            try:
                self._bot.account.stop_io()
            except Exception:
                pass
        if self._rpc:
            try:
                self._rpc.close()
            except Exception:
                pass
        self._started = False
        self._bot = None

    # --- Outgoing ---

    def send_message(self, chat_id: int, text: str, attachments: list = None) -> None:
        """Send a direct message to a chat."""
        if not self._account:
            raise RuntimeError("Adapter not configured")
        chat = self._account.get_chat_by_id(chat_id)
        if attachments:
            fpath = attachments[0]
            logger.debug("send_message chat=%s text_len=%d file=%s", chat_id, len(text or ""), fpath)
            # Send text and first attachment together
            result = chat.send_message(text=text or None, file=fpath)
            logger.debug("send_message: msg_id=%s", result.id)
            # Send remaining attachments separately
            for extra in attachments[1:]:
                logger.debug("send_message: extra file=%s", extra)
                chat.send_message(file=extra)
        else:
            chat.send_text(text)

    def create_channel(self, title: str) -> ChannelRef:
        """Create a Delta Chat broadcast list (called "Channel" in UI)."""
        if not self._account:
            raise RuntimeError("Adapter not configured")
        chat = self._account.create_broadcast(title)
        snapshot = chat.get_basic_snapshot()
        return ChannelRef(snapshot.id, chat, title)

    def get_invite_link(self, channel_id: int) -> str:
        """Get the invite link (QR code text) for a channel."""
        if not self._account:
            raise RuntimeError("Adapter not configured")
        chat = self._account.get_chat_by_id(channel_id)
        return chat.get_qr_code()

    def check_invite(self, url: str) -> dict:
        """Check what kind of invite/QR this URL represents.

        Returns dict with 'kind' and other fields from DC core.
        """
        if not self._account:
            raise RuntimeError("Adapter not configured")
        return self._account.check_qr(url.strip())

    def get_bot_invite(self) -> str:
        """Get the bot's own SecureJoin invite link/QR code text.

        Returns a string like https://i.delta.chat/#... that other users
        can scan to add the bot as a contact.
        """
        if not self._account:
            raise RuntimeError("Adapter not configured")
        return self._account.get_qr_code()

    def join_via_link(self, url: str) -> dict:
        """Join a group/channel via invite link to collect info, then leave.

        Temporarily joins the chat to fetch name, description, tags and avatar.
        Leaves the chat immediately after collecting data so the bot doesn't
        stay in groups/channels and isn't affected by new messages.

        Returns a dict with chat info after successful join:
            {id, name, type, profile_image, description, tags}
        """
        if not self._account:
            raise RuntimeError("Adapter not configured")
        url = url.strip()
        logger.info("Joining via invite: %s...", url[:80])
        # First check what kind of QR/invite this is
        qr_info = self._account.check_qr(url)
        logger.debug("QR check result: %s", qr_info)
        kind = qr_info.get("kind", "")
        if kind in ("Error",):
            raise ValueError(
                f"Неподдерживаемый тип ссылки: {kind}. "
                f"Используйте ссылку-приглашение из Delta Chat "
                f"(https://i.delta.chat/#...)"
            )
        # Pass the full URL to secure_join
        logger.info("Calling secure_join with full URL...")
        chat = self._account.secure_join(url)
        snapshot = chat.get_basic_snapshot()
        chat_id = snapshot.id
        # Wait for handshake to complete
        import time
        time.sleep(4)
        # Force IMAP sync to fetch group info
        try:
            self._account.stop_io()
            self._account.start_io()
            time.sleep(2)
        except Exception:
            pass
        # Get updated snapshot after join
        snapshot = chat.get_basic_snapshot()
        chat_name = snapshot.name
        chat_type = snapshot.chat_type
        avatar_path = snapshot.profile_image
        logger.debug("After join: name=%s type=%s avatar=%s", chat_name, chat_type, avatar_path)

        # Check if this is a 1:1 chat (bot) — chat_type 100 = DC_CHAT
        is_dm = (chat_type == 100)

        # For 1:1 chats (bots), get contact info instead of chat info
        contact_name = ""
        contact_avatar = None
        contact_status = ""
        if is_dm:
            try:
                contacts = chat.get_contacts()
                for c in contacts:
                    snap = c.get_snapshot()
                    if snap.get("id") != 1:  # not self-contact (SELF = 1)
                        contact_name = snap.get("display_name") or snap.get("name", "")
                        contact_avatar = snap.get("profile_image")
                        contact_status = snap.get("status", "")
                        logger.debug("Bot contact: name=%s avatar=%s status=%s",
                                     contact_name, contact_avatar, contact_status)
                        break
                # Use contact avatar as chat avatar for bots
                if contact_avatar and not avatar_path:
                    avatar_path = contact_avatar
                # Use contact name for chat name if available
                if contact_name and not chat_name:
                    chat_name = contact_name

                # For bot-type chats, use the bot's OWN avatar instead of the contact's
                # (contact_avatar above is the USER's avatar, not the bot's)
                if is_dm:
                    self_snap = self._account.self_contact.get_snapshot()
                    bot_avatar = self_snap.profile_image
                    if bot_avatar:
                        avatar_path = bot_avatar
                        logger.debug("Using bot self-avatar: %s", bot_avatar)
            except Exception as e:
                logger.debug("Failed to get bot contact info: %s", e)

        # Try to get description and tags from DC database directly
        description = ""
        tags = ""
        if not is_dm:
            # Query DC SQLite database for the description (groups/channels only)
            try:
                import sqlite3
                from pathlib import Path
                profile_dir = Path(str(self._profile_dir))
                for acct_dir in profile_dir.iterdir():
                    if not acct_dir.is_dir():
                        continue
                    db_path = acct_dir / "dc.db"
                    if not db_path.exists():
                        continue
                    try:
                        conn = sqlite3.connect(str(db_path))
                        row = conn.execute(
                            "SELECT description FROM chats_descriptions WHERE chat_id=?",
                            (chat_id,),
                        ).fetchone()
                        conn.close()
                        if row and row[0]:
                            description = row[0].strip()
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.debug("DB description query failed: %s", e)
        else:
            # For bots, use contact status text as description if available
            description = contact_status or ""

        # If no description yet, try to get first meaningful message
        if not description:
            _skip_prefixes = (
                "messages are end-to-end encrypted", "invited you to join",
                "you joined the", "member me added", "waiting for the device",
            )
            for attempt in range(5):
                try:
                    msgs = chat.get_messages()
                    logger.debug("get_messages attempt %d: %d msgs", attempt + 1, len(msgs))
                    for msg in msgs:
                        snap = msg.get_snapshot()
                        txt = (snap.text or "").strip()
                        if txt and not txt.lower().startswith(_skip_prefixes):
                            description = txt
                            break
                    if description:
                        break
                except Exception as e:
                    logger.debug("get_messages attempt %d: %s", attempt + 1, e)
                    # Trigger IMAP resync before retry
                    try:
                        self._account.stop_io()
                        self._account.start_io()
                    except Exception:
                        pass
                    time.sleep(3)

        # For bots, also try receiving fresh messages via wait_for_message
        if is_dm and not description:
            logger.debug("Trying to receive bot welcome message via event queue...")
            for attempt in range(3):
                try:
                    msg = self.wait_for_message(timeout=5.0)
                    if msg and msg.get("text"):
                        txt = msg["text"].strip()
                        if not txt.lower().startswith(_skip_prefixes):
                            description = txt
                            logger.debug("Got bot welcome msg: %s", txt[:100])
                            break
                except Exception:
                    time.sleep(2)

        # Extract #hashtags from description
        if description:
            hashtags = []
            for word in description.split():
                if word.startswith("#") and len(word) > 1:
                    tag = word.strip(".,!?;:\"'()[]{}").lower()
                    if tag.startswith("#"):
                        hashtags.append(tag[1:])
            if hashtags:
                tags = ", ".join(hashtags)
        logger.info(
            "Joined chat #%s: name=%s description_len=%d tags=%s avatar=%s",
            chat_id, chat_name, len(description), tags or "(none)", avatar_path,
        )

        # Leave the group/channel after collecting data
        try:
            chat.leave()
            logger.info("Left chat #%s after collecting info", chat_id)
        except Exception as e:
            logger.debug("Failed to leave chat #%s: %s", chat_id, e)

        return {
            "id": chat_id,
            "name": chat_name or "Бот",
            "type": chat_type,
            "profile_image": avatar_path,
            "description": description,
            "tags": tags,
        }

    def get_bot_invite_svg(self) -> tuple:
        """Get the bot's own SecureJoin invite as SVG + QR text.

        Returns (svg_string, qr_text).
        """
        if not self._account:
            raise RuntimeError("Adapter not configured")
        return self._account.get_qr_code_svg()

    def publish_to_channel(
        self, channel_id: int, text: str, attachments: list = None
    ) -> None:
        """Publish a message to a broadcast channel."""
        if not self._account:
            raise RuntimeError("Adapter not configured")
        logger.debug("publish_to_channel id=%s type=%s", channel_id, type(channel_id).__name__)
        chat = self._account.get_chat_by_id(channel_id)
        if attachments:
            from deltachat_rpc_client.const import ViewType
            if text:
                chat.send_message(text=text, file=attachments[0], viewtype=ViewType.IMAGE)
            else:
                chat.send_message(file=attachments[0], viewtype=ViewType.IMAGE)
            for fpath in attachments[1:]:
                chat.send_message(file=fpath, viewtype=ViewType.IMAGE)
        elif text:
            chat.send_text(text)

    # --- Incoming events ---

    def wait_for_message(self, timeout: float = 2.0) -> Optional[dict]:
        """
        Wait for an incoming message with timeout.
        
        Messages are delivered by the Bot event loop running
        in a background thread, which properly handles all
        DC core events including SecureJoin handshakes.
        """
        try:
            return self._msg_queue.get(timeout=timeout)
        except _queue.Empty:
            return None

    def force_check_incoming(self) -> None:
        """
        Force the DC core to scan for new messages directly,
        bypassing the event system.

        This is needed because in bot mode, the DC core may not
        maintain a persistent IMAP IDLE connection and relies on
        periodic polling. Calling this method ensures messages
        are picked up even if no INCOMING_MSG event was generated.
        """
        if not self._account:
            return
        try:
            # Force IMAP reconnect so DC core rescans the inbox
            self._imap_poll()
            msgs = self._account.get_next_messages()
            if msgs:
                logger.debug("force_check found %d messages", len(msgs))
            for message in msgs:
                snapshot = message.get_snapshot()
                # Skip self-sent (id=1) and device (id=5) messages
                if snapshot.from_id in (1, 5):
                    try:
                        message.mark_seen()
                    except Exception:
                        pass
                    continue
                # Deduplicate: skip if we already processed this message_id
                msg_id = snapshot.id
                if hasattr(self, "_fc_last_id") and self._fc_last_id == msg_id:
                    continue
                self._fc_last_id = msg_id
                # Process as new message (same logic as _on_new_message)
                try:
                    chat = self._account.get_chat_by_id(snapshot.chat_id)
                    chat.accept()
                except Exception:
                    pass
                sender = self._account.get_contact_by_id(snapshot.from_id)
                sender_addr = getattr(sender.get_snapshot(), "address", "")
                msg = {
                    "message_id": snapshot.id,
                    "text": snapshot.text,
                    "sender_id": str(snapshot.from_id),
                    "sender_addr": sender_addr,
                    "chat_id": snapshot.chat_id,
                }
                self._msg_queue.put(msg)
                try:
                    message.mark_seen()
                except Exception:
                    pass
        except Exception as e:
            logger.debug("force_check_incoming: %s", e)

    # --- Avatar / Image ---

    def set_chat_image(self, chat_id: int, image_path: str) -> None:
        """Set a chat's avatar image via DC core."""
        if not self._account:
            raise RuntimeError("Adapter not configured")
        chat = self._account.get_chat_by_id(chat_id)
        chat.set_image(image_path)

    def set_account_avatar(self, image_path: str) -> None:
        """Set bot's own profile avatar."""
        if not self._account:
            raise RuntimeError("Adapter not configured")
        self._account.set_avatar(image_path)

    # --- Cleanup / Storage ---

    def cleanup_old_messages(self, max_age_days: int = 7) -> int:
        """Delete messages older than max_age_days from all chats.

        Returns the number of deleted messages.
        """
        if not self._account:
            return 0
        import time
        cutoff = time.time() - max_age_days * 86400
        total_deleted = 0
        try:
            chats = self._account.get_chatlist(no_specials=True)
            for chat in chats:
                try:
                    msgs = chat.get_messages()
                    to_delete = []
                    for msg in msgs:
                        try:
                            snap = msg.get_snapshot()
                            ts = getattr(snap, "timestamp", 0)
                            if ts > 0 and ts < cutoff:
                                to_delete.append(msg)
                        except Exception:
                            continue
                    if to_delete:
                        self._account.delete_messages(to_delete)
                        total_deleted += len(to_delete)
                        logger.info(
                            "Cleanup: deleted %d old messages from chat #%s",
                            len(to_delete), chat.id,
                        )
                except Exception as e:
                    logger.debug("Cleanup chat #%s: %s", getattr(chat, "id", "?"), e)
        except Exception as e:
            logger.error("Cleanup failed: %s", e)
        return total_deleted

    def get_storage_stats(self) -> dict:
        """Return storage usage stats for the bot's DC profile."""
        import shutil
        from pathlib import Path
        profile = Path(self._profile_dir)
        stats = {}
        try:
            total_bytes = sum(
                f.stat().st_size for f in profile.rglob("*") if f.is_file()
            )
            stats["profile_bytes"] = total_bytes
            stats["profile_human"] = _format_bytes(total_bytes)
        except Exception as e:
            stats["profile_error"] = str(e)
        return stats
