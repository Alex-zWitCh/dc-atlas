"""
Fake Delta Chat adapter for testing without a real Delta Chat account.

All methods operate in-memory. Channels exist as Python objects.
"""


class ChannelRef:
    """Reference to a fake channel."""

    def __init__(self, channel_id: str, title: str):
        self.id = channel_id
        self.title = title


class FakeDeltaChatAdapter:
    """
    Simulates Delta Chat API for development and testing.

    Replace with real DeltaChatAdapter before production deployment.
    """

    def __init__(self, bot_name: str = "DC Atlas"):
        self._bot_name = bot_name
        self._channels: dict[str, ChannelRef] = {}
        self._channel_counter = 0
        self._published_messages: list[dict] = []
        self._inbox: list[dict] = []

    # --- Outgoing ---

    def send_message(self, chat_id: str, text: str, attachments: list = None) -> None:
        """Simulate sending a message to a chat."""
        self._published_messages.append(
            {
                "type": "dm",
                "chat_id": chat_id,
                "text": text,
                "attachments": attachments or [],
            }
        )

    def create_channel(self, title: str) -> ChannelRef:
        """Simulate creating a Delta Chat Channel."""
        self._channel_counter += 1
        channel_id = f"ch_{self._channel_counter}"
        ref = ChannelRef(channel_id, title)
        self._channels[channel_id] = ref
        return ref

    def get_invite_link(self, channel_id: str) -> str:
        """Simulate getting an invite link for a channel."""
        if channel_id not in self._channels:
            raise ValueError(f"Channel {channel_id} not found")
        return f"https://i.delta.chat/#fake_{channel_id}"

    def publish_to_channel(
        self, channel_id: str, text: str, attachments: list = None
    ) -> None:
        """Simulate publishing a message to a channel."""
        if channel_id not in self._channels:
            raise ValueError(f"Channel {channel_id} not found")
        self._published_messages.append(
            {
                "type": "channel",
                "channel_id": channel_id,
                "text": text,
                "attachments": attachments or [],
            }
        )

    # --- Incoming ---

    def receive_message(self, sender_id: str, text: str) -> None:
        """Simulate receiving a message from a user."""
        self._inbox.append({"sender": sender_id, "text": text})

    # --- Avatar / Image ---

    def set_chat_image(self, chat_id: str, image_path: str) -> None:
        """Simulate setting a chat's avatar image."""
        self._last_set_image = {"chat_id": chat_id, "path": image_path}

    def set_account_avatar(self, image_path: str) -> None:
        """Simulate setting bot's own profile avatar."""
        self._bot_avatar = image_path

    # --- Queries ---

    def get_published_messages(self, channel_id: str = None) -> list[dict]:
        """Return published messages, optionally filtered by channel."""
        if channel_id:
            return [
                m
                for m in self._published_messages
                if m.get("channel_id") == channel_id
            ]
        return list(self._published_messages)

    def wait_for_message(self, timeout: float = 2.0):
        """Simulate waiting for an incoming message.

        Returns None (no incoming messages in fake mode).
        Compatible with production loop.
        """
        if self._inbox:
            msg = self._inbox.pop(0)
            return {
                "chat_id": msg["sender"],
                "sender_addr": msg["sender"],
                "text": msg["text"],
            }
        return None

    def reset(self) -> None:
        """Clear all state for a fresh test."""
        self._channels.clear()
        self._channel_counter = 0
        self._published_messages.clear()
        self._inbox.clear()
