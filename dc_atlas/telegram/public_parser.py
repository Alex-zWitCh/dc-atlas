"""
Telegram public page parser for DC Atlas.

Fetches and parses posts from t.me/s/<channel> pages.
Isolated module — if Telegram HTML changes, only this file needs fixing.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import get_config

logger = logging.getLogger(__name__)


class TelegramPost:
    """A single post from a Telegram channel."""

    def __init__(
        self,
        post_id: int,
        text: str,
        original_url: str,
        published_at: Optional[str] = None,
        has_photo: bool = False,
        has_video: bool = False,
        has_file: bool = False,
        photo_urls: Optional[list[str]] = None,
        video_thumb_urls: Optional[list[str]] = None,
        total_photo_count: int = 0,
        total_video_count: int = 0,
    ):
        self.post_id = post_id
        self.text = text
        self.original_url = original_url
        self.published_at = published_at
        self.has_photo = has_photo
        self.has_video = has_video
        self.has_file = has_file
        self.photo_urls = photo_urls or []
        self.video_thumb_urls = video_thumb_urls or []
        self.total_photo_count = total_photo_count
        self.total_video_count = total_video_count

    def __repr__(self):
        return (
            f"TelegramPost(id={self.post_id}, text_len={len(self.text)}, "
            f"photo={self.has_photo})"
        )


class FetchError(Exception):
    """Raised when a channel page cannot be fetched."""

    pass


class TelegramPublicParser:
    """Fetches and parses t.me/s/<channel> pages."""

    def __init__(self):
        cfg = get_config()
        self._base_url = cfg.TELEGRAM_PUBLIC_BASE_URL
        self._timeout = cfg.POLL_HTTP_TIMEOUT_SECONDS
        self._max_photos = cfg.TELEGRAM_MAX_PHOTOS_PER_POST
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            }
        )

        # Optional HTTP proxy (bypass Telegram blocks)
        if cfg.TELEGRAM_PROXY_ENABLED:
            proxy_url = cfg.TELEGRAM_PROXY_URL
            self._session.trust_env = False
            self._session.proxies.update({
                "http": proxy_url,
                "https": proxy_url,
            })
            logger.info("Telegram proxy enabled: %s", proxy_url)

    def fetch_channel(self, username: str) -> list[TelegramPost]:
        """
        Fetch and parse all visible posts from a public Telegram channel.

        Args:
            username: Telegram channel username (without @).

        Returns:
            List of TelegramPost objects, newest first.

        Raises:
            FetchError: If the page cannot be accessed.
        """
        url = f"{self._base_url}/{username}"
        logger.debug("Fetching %s", url)

        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            raise FetchError(f"Failed to fetch {url}: {e}")

        if response.status_code == 404:
            raise FetchError(f"Channel @{username} not found (404)")

        # Check for redirect to login (private channel)
        if "login" in response.url.lower() or len(response.text) < 500:
            raise FetchError(f"Channel @{username} is not public or requires login")

        return self._parse_page(response.text, username)

    def parse(self, html: str) -> list[TelegramPost]:
        """Parse Telegram channel HTML (for testing with fixtures)."""
        return self._parse_page(html, "test")

    def _parse_page(self, html: str, username: str) -> list[TelegramPost]:
        """Parse HTML and extract posts."""
        soup = BeautifulSoup(html, "html.parser")
        posts = []

        # Telegram uses <div class="tgme_widget_message" data-post="..."> for each post
        # (No longer wrapped in tgme_widget_message_wrapper as of 2025+)
        message_divs = soup.find_all("div", class_="tgme_widget_message")

        if not message_divs:
            logger.warning("No posts found on page for %s", username)
            return []

        for message_div in message_divs:
            try:
                post = self._parse_single_post(message_div, username)
                if post:
                    posts.append(post)
            except Exception as e:
                logger.warning("Failed to parse a post on %s: %s", username, e)
                continue

        return posts

    def _parse_single_post(
        self, message_div, username: str
    ) -> Optional[TelegramPost]:
        """Parse a single tgme_widget_message div."""
        # Post ID from data-post
        data_post = message_div.get("data-post", "")
        if not data_post:
            return None

        # data-post format: "username/post_id"
        parts = data_post.split("/")
        if len(parts) < 2:
            return None
        try:
            post_id = int(parts[-1])
        except ValueError:
            return None

        # Text
        text_div = message_div.find(
            "div", class_="tgme_widget_message_text"
        )
        text = text_div.get_text("\n", strip=True) if text_div else ""

        # Date
        time_tag = message_div.find("time")
        published_at = None
        if time_tag and time_tag.has_attr("datetime"):
            published_at = time_tag["datetime"]

        # Media detection (updated HTML structure as of 2025+)
        import re as _re
        photo_urls = []
        video_thumb_urls = []
        # Extract photo URLs from background-image style
        for wrap in message_div.find_all("a", class_="tgme_widget_message_photo_wrap"):
            style = wrap.get("style", "")
            m = _re.search(r"background-image:url\(['\"]([^'\"]+)['\"]\)", style)
            if m:
                photo_urls.append(m.group(1))
        # Extract video thumbnail URLs
        for thumb in message_div.find_all("i", class_="tgme_widget_message_video_thumb"):
            style = thumb.get("style", "")
            m = _re.search(r"background-image:url\(['\"]([^'\"]+)['\"]\)", style)
            if m:
                video_thumb_urls.append(m.group(1))
        # Count total videos (including grouped)
        total_video_count = len(
            message_div.find_all("a", class_="tgme_widget_message_video_player") or []
        ) or (1 if message_div.find("video") else 0)
        # Count total photos (photo_wrap divs)
        photo_wraps = message_div.find_all("a", class_="tgme_widget_message_photo_wrap")
        total_photo_count = max(len(photo_wraps), len(photo_urls))
        # Deduplicate photo_urls by unique URL
        seen = set()
        unique_photos = []
        for u in photo_urls:
            if u not in seen:
                seen.add(u)
                unique_photos.append(u)
        photo_urls = unique_photos
        has_photo = bool(photo_urls) or bool(
            message_div.find("a", class_="tgme_widget_message_photo_wrap")
            or message_div.find("a", class_="tgme_widget_message_photo")
            or message_div.find("i", class_="photo")
        )
        has_video = bool(
            message_div.find("video")
            or message_div.find("a", class_="tgme_widget_message_video")
            or bool(
                message_div.find(
                    "div", class_="message_media_not_supported"
                )
                and "video" in (
                    message_div.get_text() or ""
                ).lower()
            )
        )
        has_file = bool(
            message_div.find("a", class_="tgme_widget_message_document")
        )

        original_url = f"https://t.me/{data_post}"

        return TelegramPost(
            post_id=post_id,
            text=text,
            original_url=original_url,
            published_at=published_at,
            has_photo=has_photo,
            has_video=has_video,
            has_file=has_file,
            photo_urls=photo_urls,
            video_thumb_urls=video_thumb_urls,
            total_photo_count=total_photo_count,
            total_video_count=total_video_count,
        )
