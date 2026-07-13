"""
Command router for DC Atlas.

Parses incoming messages and dispatches to the appropriate service.
"""

import logging
import re
from pathlib import Path
from typing import Optional

import time

from ..services.catalog_service import CatalogService
from ..services.moderation_service import ModerationService
from ..services.rate_limiter import DEFAULT_RULES, RateLimitService
from ..services.telegram_mirror_service import TelegramMirrorService
from ..storage.sqlite_storage import SQLiteStorage
from . import formatters as fmt


class CommandRouter:
    """Routes user messages to service methods."""

    def __init__(
        self,
        catalog: CatalogService,
        adapter: any,
        storage: SQLiteStorage,
    ):
        self._catalog = catalog
        self._adapter = adapter
        self._storage = storage
        self._moderation = ModerationService(storage)
        self._tg_mirror = TelegramMirrorService(catalog, adapter, storage)
        self._rate_limiter = RateLimitService()
        self._logger = logging.getLogger(__name__)
        # Pending user confirmations: {user_id: {"action": ..., "item_id": ...}}
        self._pending = {}
        # Track user chat_ids for async follow-ups: {user_id: chat_id}
        self._user_chats = {}

    def _check_rate(self, user_id: str, action: str) -> Optional[str]:
        """Check rate limit. Returns error message if blocked."""
        rule = DEFAULT_RULES.get(action)
        if not rule:
            return None
        allowed, _ = self._rate_limiter.check(f"user:{user_id}:{action}", rule)
        if not allowed:
            return fmt.format_error(
                f"Слишком много запросов. Подождите немного."
            )
        return None

    def handle(self, user_id: str, text: str, chat_id: Optional[int] = None, is_direct_chat: bool = True) -> Optional[str]:
        """Process an incoming message and return a response."""
        text = text.strip()

        # Store user's chat_id for async follow-ups
        if chat_id is not None:
            self._user_chats[user_id] = chat_id

        if not text.startswith("/"):
            # Auto-detect links only in direct 1:1 chats.
            # Never process ordinary group/channel messages.
            if not is_direct_chat:
                return None

            try:
                result = self._auto_add_from_url(user_id, text, chat_id)
                if result is not None:
                    return result
            except Exception as e:
                self._logger.exception("Auto-detect error: %s", e)
                return fmt.format_error(f"Ошибка: {e}")

            self._logger.info(
                "Sending welcome to %s in chat %s (direct=%s)",
                user_id,
                chat_id,
                is_direct_chat,
            )
            return fmt.format_welcome()

        # Support both "/open 123" and "/open_123" syntax
        # Also "/report_5" etc.
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Underscore syntax: /open_123, /report_18 спам, /admin_contact_27 https://...
        # Must handle multi-part commands like /admin_contact, /admin_dismiss_report
        _underscore_commands = (
            "/open", "/report", "/delete", "/list",
            "/admin_hide", "/admin_show", "/admin_delete_full", "/admin_delete",
            "/admin_dismiss_report", "/admin_contact", "/set_contact",
        )
        if "_" in command:
            for base_cmd in _underscore_commands:
                prefix = base_cmd + "_"
                if command.startswith(prefix):
                    suffix = command[len(prefix):]
                    command = base_cmd
                    if args:
                        args = suffix + " " + args
                    else:
                        args = suffix
                    break

        handler = self._get_handler(command)
        if handler is None:
            return f'Неизвестная команда: {command}\n/help — список команд'

        try:
            # Pass chat_id to handlers that need it (like /open)
            if command in ("/open",) and chat_id is not None:
                return handler(user_id, args, chat_id)
            return handler(user_id, args)
        except Exception as e:
            return fmt.format_error(str(e))

    def _get_handler(self, command: str):
        handlers = {
            "/help": self._cmd_help,
            "/start": self._cmd_help,
            "/search": self._cmd_search,
            "/list": self._cmd_list,
            "/open": self._cmd_open,
            "/new": self._cmd_new,
            "/report": self._cmd_report,
            "/my": self._cmd_my,
            "/delete": self._cmd_delete,
            "/admin_stats": self._cmd_admin_stats,
            "/admin_hide": self._cmd_admin_hide,
            "/admin_show": self._cmd_admin_show,
            "/admin_delete_full": self._cmd_admin_delete_full,
            "/admin_delete": self._cmd_admin_delete,
            "/admin_sources": self._cmd_admin_sources,
            "/admin_pause_tg": self._cmd_admin_pause_tg,
            "/admin_resume_tg": self._cmd_admin_resume_tg,
            "/admin_check_tg": self._cmd_admin_check_tg,
            "/admin_reports": self._cmd_admin_reports,
            "/admin_dismiss_report": self._cmd_admin_dismiss_report,
            "/admin_clear_reports": self._cmd_admin_clear_reports,
            "/admin_contact": self._cmd_admin_contact,
            "/set_contact": self._cmd_set_contact,
            "/admin_proxy": self._cmd_admin_proxy,
            "/invite": self._cmd_invite,
        }
        return handlers.get(command)

    def _is_admin(self, user_id: str) -> bool:
        from ..config import get_config
        return user_id in get_config().BOT_ADMIN_EMAILS

    def _auto_add_from_url(self, user_id: str, text: str, chat_id: Optional[int] = None) -> Optional[str]:
        """Try to auto-detect and add a link as a catalog item.

        Returns response string if link was recognized, None otherwise.
        """
        url = text.strip()

        # 1. Telegram links → telegram_mirror
        tg_match = re.match(
            r"^(?:https?://)?(?:www\.)?t\.me/([a-zA-Z0-9_]+)",
            url,
            re.IGNORECASE,
        )
        if tg_match:
            return self._cmd_add_tg(user_id, url)

        # 2. Delta Chat invite links → determine type from URL params
        dc_match = re.match(
            r"^(?:https?://)?i\.delta\.chat/#(.+)$",
            url,
        )
        if dc_match:
            fragment = dc_match.group(1)
            import urllib.parse
            params = urllib.parse.parse_qs(fragment)

            has_a = "a" in params  # email address
            has_b = "b" in params  # broadcast/channel name → channel/group
            has_g = "g" in params  # group ID → group
            has_n = "n" in params  # display name

            if has_b:
                # Has &b= (channel/group name) → channel or group
                return self._cmd_add_channel(user_id, url, chat_id)
            elif has_g:
                # Has &g= (group ID) → group
                return self._cmd_add_group(user_id, url, chat_id)
            elif has_a and not has_b:
                # Has &a= but no &b= → contact/bot link
                return self._cmd_add_bot(user_id, url)
            else:
                # No distinguishing params — try check_qr to hint
                try:
                    qr_info = self._adapter.check_invite(url)
                    kind = qr_info.get("kind", "")
                    if kind == "askVerifyContact":
                        return self._cmd_add_bot(user_id, url)
                except Exception:
                    pass
                # Default: treat as group (async enrich will correct if needed)
                return self._cmd_add_group(user_id, url, chat_id)

        # 3. Plain email addresses → could be a bot
        email_match = re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", url)
        if email_match and "@" in url:
            return self._cmd_add_bot(user_id, url)

        # Not a recognized link
        return None

    def _resolve_user_id(self, dc_contact_id: str) -> str:
        """Convert DC contact ID to internal users.id (as string for DB queries)."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        user = self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?", (dc_contact_id,)
        )
        if user:
            return str(user["id"])
        self._storage.execute(
            "INSERT INTO users (dc_contact_id, display_name, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (dc_contact_id, dc_contact_id, now, now),
        )
        user = self._storage.fetchone(
            "SELECT id FROM users WHERE dc_contact_id = ?", (dc_contact_id,)
        )
        return str(user["id"])

    # ---- User commands ----

    def _cmd_help(self, user_id: str, args: str) -> str:
        return fmt.format_help(is_admin=self._is_admin(user_id))

    def _cmd_add_group(self, user_id: str, args: str, reply_chat_id: Optional[int] = None) -> str:
        blocked = self._check_rate(user_id, "add_group")
        if blocked:
            return blocked
        if not args:
            return (
                "Чтобы добавить группу, отправьте ссылку-приглашение:\n"
                "/add_group https://i.delta.chat/#...\n\n"
                "Бот вступит в группу, скопирует название, описание и теги (#хештеги)."
            )
        # Take only the first part (ignore any | admin_contact for groups)
        invite_url = args.strip().split("|", 1)[0].strip()
        if not invite_url:
            return "Укажите ссылку-приглашение."

        # Check for duplicate by invite_url
        existing = self._catalog.find_by_invite(invite_url)
        if existing:
            return (
                f"Такая группа уже есть в каталоге — карточка #{existing['id']}:\n"
                f"{existing['title']}\n"
                f"/open_{existing['id']}"
            )

        item = self._catalog.create_item(
            item_type="deltachat_group",
            title="(загрузка...)",
            join_mode="open",
            invite_url=invite_url,
            created_by_user_id=self._resolve_user_id(user_id),
        )
        item_id = item["id"]
        self._async_enrich_chat(invite_url, item_id, user_id, reply_chat_id)
        return f"✅ Карточка #{item_id} (группа) создана.\nДанные загружаются... /open_{item_id}"

    def _cmd_add_channel(self, user_id: str, args: str, reply_chat_id: Optional[int] = None) -> str:
        blocked = self._check_rate(user_id, "add_channel")
        if blocked:
            return blocked
        if not args:
            return (
                "Чтобы добавить канал, отправьте ссылку-приглашение:\n"
                "/add_channel https://i.delta.chat/#...\n\n"
                "Бот вступит в канал, скопирует название, описание, аватар и теги (#хештеги).\n"
                "Опционально можно указать контакт администратора после ссылки:\n"
                "/add_channel https://i.delta.chat/#... | https://i.delta.chat/#contact_link"
            )
        # Parse: invite_url | admin_contact
        parts = [p.strip() for p in args.split("|", 1)]
        invite_url = parts[0]
        admin_contact = parts[1] if len(parts) > 1 else None

        # Validate admin contact must be a Delta Chat contact link
        if admin_contact and not admin_contact.startswith("https://i.delta.chat/#"):
            return (
                f"❌ Контакт администратора должен быть ссылкой-приглашением Delta Chat, "
                f"например:\n"
                f"https://i.delta.chat/#396AEA25ED9A9EA48CDA8A6011C844075E9791B6&v=3&...\n"
                f"Это ваш личный контакт из Delta Chat (поделиться → скопировать ссылку).\n"
                f"Обычный email не принимается."
            )

        # Check for duplicate by invite_url
        existing = self._catalog.find_by_invite(invite_url)
        if existing:
            return (
                f"Такой канал уже есть в каталоге — карточка #{existing['id']}:\n"
                f"{existing['title']}\n"
                f"/open_{existing['id']}"
            )

        item = self._catalog.create_item(
            item_type="deltachat_channel",
            title="(загрузка...)",
            join_mode="open",
            invite_url=invite_url,
            admin_contact=admin_contact,
            created_by_user_id=self._resolve_user_id(user_id),
        )
        item_id = item["id"]
        self._async_enrich_chat(invite_url, item_id, user_id, reply_chat_id)
        return f"✅ Карточка #{item_id} (канал) создана.\nДанные загружаются... /open_{item_id}\n\nЕсли хотите указать контакт администратора, отправьте:\n/admin_contact_{item_id} https://i.delta.chat/#..."

    def _cmd_add_tg(self, user_id: str, args: str) -> str:
        blocked = self._check_rate(user_id, "add_tg")
        if blocked:
            return blocked
        if not args:
            return "Укажите ссылку на Telegram-канал:\n/add_tg https://t.me/example"
        import logging
        logger = logging.getLogger(__name__)
        try:
            result = self._tg_mirror.add_source(args.strip(), user_id)
            if result:
                logger.info("TG mirror added: %s -> %s..", args.strip()[:40], result[:60])
            return result or "✅ Telegram-зеркало добавлено."
        except Exception as e:
            logger.exception("TG mirror add failed: %s", e)
            return fmt.format_error(f"Ошибка при добавлении: {e}")

    def _cmd_add_bot(self, user_id: str, args: str) -> str:
        blocked = self._check_rate(user_id, "add_group")
        if blocked:
            return blocked
        if not args:
            return (
                "Чтобы добавить бота, отправьте ссылку-контакт:\n"
                "/add_bot https://i.delta.chat/#...\n\n"
                "Ссылку можно получить из профиля бота: Поделиться → Скопировать ссылку."
            )
        url = args.strip()

        # Parse info directly from URL (a=email, n=name)
        import urllib.parse
        bot_name = ""
        bot_addr = ""
        if "#" in url:
            fragment = url.split("#", 1)[1]
            params = urllib.parse.parse_qs(fragment)
            bot_name = params.get("n", [""])[0]
            bot_addr = params.get("a", [""])[0]
        title = urllib.parse.unquote_plus(bot_name) if bot_name else bot_addr or "Бот"
        description = f"Бот: {bot_addr}" if bot_addr else ""

        # Try to validate via check_qr and get more info
        try:
            qr_info = self._adapter.check_invite(url)
            self._logger.debug("Bot QR info: %s", qr_info)
            if "name" in qr_info:
                title = qr_info["name"]
        except Exception as e:
            self._logger.debug("QR check failed for bot: %s", e)

        item = self._catalog.create_item(
            item_type="bot",
            title=title,
            description=description,
            invite_url=url,
            created_by_user_id=self._resolve_user_id(user_id),
        )
        item_id = item["id"]

        # Start background task to enrich bot data (avatar, description)
        reply_id = self._user_chats.get(user_id)
        self._async_enrich_chat(url, item_id, user_id, reply_id)

        return fmt.format_item_added(item_id, "bot")

    def _cmd_search(self, user_id: str, args: str) -> str:
        blocked = self._check_rate(user_id, "search")
        if blocked:
            return blocked
        if not args:
            return "Укажите поисковый запрос:\n/search stm32"

        # Parse type filter
        item_type = None
        type_match = re.match(r"тип:(\S+)\s*(.*)", args, re.IGNORECASE)
        if type_match:
            type_map = {
                "группа": "deltachat_group",
                "канал": "deltachat_channel",
                "зеркало": "telegram_mirror",
                "бот": "bot",
                "группы": "deltachat_group",
                "каналы": "deltachat_channel",
            }
            item_type = type_map.get(type_match.group(1).lower())
            query = type_match.group(2).strip()
        else:
            query = args

        results = self._catalog.search(query=query, item_type=item_type)
        return fmt.format_search_results(results["items"], results["total"])

    def _cmd_open(self, user_id: str, args: str, chat_id: Optional[int] = None) -> Optional[str]:
        try:
            item_id = int(args.strip())
        except (ValueError, IndexError):
            return "Укажите ID карточки:\n/open 184"

        item = self._catalog.get_item(item_id)
        if not item:
            return f"Карточка #{item_id} не найдена."

        card_text = fmt.format_item_card(item)

        # Send avatar image attached to the card text if available
        if chat_id is not None:
            avatar_path = item.get("avatar_file_path")
            self._logger.debug("_cmd_open #%d: chat_id=%s avatar_path=%s", item_id, chat_id, avatar_path)
            if avatar_path:
                try:
                    self._adapter.send_message(chat_id, card_text, [avatar_path])
                    self._logger.debug("_cmd_open #%d: message sent with image", item_id)
                    return None  # Already sent
                except Exception as ex:
                    self._logger.error("_cmd_open #%d: failed: %s", item_id, ex)

        return card_text

    def _cmd_list(self, user_id: str, args: str) -> str:
        """List all catalog items, paginated 20 per page."""
        try:
            page = int(args.strip()) if args.strip() else 1
        except ValueError:
            page = 1
        result = self._catalog.list_all_paginated(page=page, page_size=20)
        return fmt.format_list(result)

    def _cmd_new(self, user_id: str, args: str) -> str:
        items = self._catalog.list_new(limit=10)
        return fmt.format_new_items(items)

    def _cmd_invite(self, user_id: str, args: str) -> str:
        """Send the bot's invite link with QR code."""
        chat_id = self._user_chats.get(user_id)
        if not chat_id:
            return "Не удалось определить ваш чат. Попробуйте написать любое сообщение и повторить команду."

        tmp_path = None
        try:
            invite_url = self._adapter.get_bot_invite()

            import qrcode
            import tempfile
            import os

            qr = qrcode.make(invite_url)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp.name
            qr.save(tmp_path)
            tmp.close()

            text = (
                f"🤖 DC Atlas — моя инвайт-ссылка\n\n"
                f"Отсканируй этот QR-код в Delta Chat, чтобы добавить меня "
                f"в контакты и пользоваться каталогом.\n\n"
                f"{invite_url}\n\n"
                f"Команды: /help"
            )

            self._adapter.send_message(chat_id, text, [tmp_path])
            return None

        except Exception as e:
            self._logger.error("/invite failed: %s", e)
            return "Не удалось создать инвайт-ссылку. Попробуйте позже."

        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ---- Admin commands ----

    def _cmd_admin_stats(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        total = self._storage.fetchone("SELECT COUNT(*) as cnt FROM catalog_items")
        active = self._storage.fetchone(
            "SELECT COUNT(*) as cnt FROM catalog_items WHERE status='active'"
        )
        sources = self._storage.fetchone(
            "SELECT COUNT(*) as cnt FROM telegram_sources"
        )
        reports = self._storage.fetchone(
            "SELECT COUNT(*) as cnt FROM reports WHERE status='new'"
        )

        # Try to get storage info from adapter
        storage_line = ""
        try:
            adapter = getattr(self, "_adapter", None)
            if adapter and hasattr(adapter, "get_storage_stats"):
                stats = adapter.get_storage_stats()
                if "profile_human" in stats:
                    storage_line = f"\nДанные DC: {stats['profile_human']}"
        except Exception:
            pass

        return (
            f"📊 Статистика:\n"
            f"Карточек: {total['cnt']} (активных: {active['cnt']})\n"
            f"TG-источников: {sources['cnt']}\n"
            f"Новых жалоб: {reports['cnt']}"
            f"{storage_line}"
            f"{self._proxy_status_line()}"
        )

    def _proxy_status_line(self) -> str:
        """Return a one-line proxy status for /admin_stats."""
        from ..config import get_config
        cfg = get_config()
        if cfg.TELEGRAM_PROXY_ENABLED:
            url = cfg.TELEGRAM_PROXY_URL
            # Mask credentials: http://user:pass@host → http://user:***@host
            import re
            masked = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
            return f"\nПрокси: ✅ {masked}"
        return "\nПрокси: ❌ отключён"

    def _cmd_admin_proxy(self, user_id: str, args: str) -> str:
        """Show or set Telegram proxy. Proxy is persisted in .env and bot restarts."""
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        from ..config import get_config
        import re

        def _mask(url: str) -> str:
            return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url or "")

        cfg = get_config()
        args = args.strip()

        if not args:
            if cfg.TELEGRAM_PROXY_ENABLED:
                return f"Прокси включён.\nURL: {_mask(cfg.TELEGRAM_PROXY_URL)}\n\n/admin_proxy off — отключить\n/admin_proxy on <url> — сменить"
            return "Прокси отключён.\n/admin_proxy on http://user:pass@host:port — включить"

        parts = args.split(maxsplit=1)
        action = parts[0].lower()

        if action == "off":
            cfg.TELEGRAM_PROXY_ENABLED = False
            cfg.TELEGRAM_PROXY_URL = ""
            cfg._pending_restart = True
            env_path = Path("/opt/dc-atlas/.env")
            if env_path.exists():
                text = env_path.read_text()
                text = re.sub(r"^TELEGRAM_PROXY_ENABLED=.*$", "TELEGRAM_PROXY_ENABLED=false", text, flags=re.M)
                text = re.sub(r"^TELEGRAM_PROXY_URL=.*$", "TELEGRAM_PROXY_URL=", text, flags=re.M)
                env_path.write_text(text)
            return "✅ Прокси отключён. Бот перезапускается…"

        if action == "on":
            if len(parts) < 2:
                return "Укажите URL: /admin_proxy on http://user:pass@host:port"
            url = parts[1]
            cfg.TELEGRAM_PROXY_ENABLED = True
            cfg.TELEGRAM_PROXY_URL = url
            cfg._pending_restart = True
            env_path = Path("/opt/dc-atlas/.env")
            if env_path.exists():
                text = env_path.read_text()
                text = re.sub(r"^TELEGRAM_PROXY_ENABLED=.*$", "TELEGRAM_PROXY_ENABLED=true", text, flags=re.M)
                text = re.sub(r"^TELEGRAM_PROXY_URL=.*$", f"TELEGRAM_PROXY_URL={url}", text, flags=re.M)
                env_path.write_text(text)
            return f"✅ Прокси включён: {_mask(url)}\nБот перезапускается…"

        return "/admin_proxy — статус\n/admin_proxy on <url> — включить\n/admin_proxy off — отключить"

    def _cmd_admin_hide(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        try:
            item_id = int(args.strip())
        except ValueError:
            return "Укажите ID: /admin_hide 184"
        item = self._catalog.get_item(item_id)
        if not item:
            return f"Карточка #{item_id} не найдена."
        self._moderation.admin_hide(item_id, "admin")
        return fmt.format_success(f"Карточка #{item_id} скрыта.")

    def _cmd_admin_show(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        try:
            item_id = int(args.strip())
        except ValueError:
            return "Укажите ID: /admin_show 184"
        self._moderation.admin_show(item_id)
        # Dismiss all active reports for this item so it won't re-hide
        dismissed = self._moderation.dismiss_reports_for_item(item_id)
        msg = f"Карточка #{item_id} восстановлена."
        if dismissed:
            msg += f"\n✅ {dismissed} жалоб деактивировано."
        return fmt.format_success(msg)

    def _cmd_admin_delete_full(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        try:
            item_id = int(args.strip())
        except ValueError:
            return "Укажите ID: /admin_delete_full 184"
        # Hard delete: remove from DB, clean files, reports, sources
        success = self._catalog.admin_delete_item(item_id)
        if success:
            return fmt.format_success(f"Карточка #{item_id} полностью удалена.")
        return fmt.format_error("Карточка не найдена.")

    def _cmd_admin_delete(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        try:
            item_id = int(args.strip())
        except ValueError:
            return "Укажите ID: /admin_delete 184"
        self._catalog.update_status(item_id, "deleted_by_admin")
        return fmt.format_success(f"Карточка #{item_id} удалена.")

    def _cmd_admin_sources(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        sources = self._storage.fetchall(
            "SELECT username, status, error_message, last_checked_at "
            "FROM telegram_sources ORDER BY last_checked_at ASC NULLS FIRST LIMIT 20"
        )
        if not sources:
            return "Нет Telegram-источников."
        lines = ["📡 Telegram-источники:"]
        for s in sources:
            err = f" — {s['error_message'][:40]}" if s.get("error_message") else ""
            lines.append(
                f"  @{s['username']} [{s['status']}]{err}"
            )
        return "\n".join(lines)

    def _cmd_admin_pause_tg(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        username = args.strip().lower().lstrip("@")
        if not username:
            return "Укажите username: /admin_pause_tg example"
        self._storage.execute(
            "UPDATE telegram_sources SET status='paused', updated_at=? WHERE username=?",
            (_now(), username),
        )
        return fmt.format_success(f"Источник @{username} приостановлен.")

    def _cmd_admin_resume_tg(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        username = args.strip().lower().lstrip("@")
        if not username:
            return "Укажите username: /admin_resume_tg example"
        self._storage.execute(
            "UPDATE telegram_sources SET status='active', error_message=NULL, updated_at=? WHERE username=?",
            (_now(), username),
        )
        return fmt.format_success(f"Источник @{username} возобновлён.")

    def _cmd_admin_check_tg(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        username = args.strip().lower().lstrip("@")
        if not username:
            return "Укажите username: /admin_check_tg example"
        source = self._storage.fetchone(
            "SELECT * FROM telegram_sources WHERE username=?", (username,)
        )
        if not source:
            return f"Источник @{username} не найден в каталоге."
        return (
            f"Источник: @{source['username']}\n"
            f"Статус: {source['status']}\n"
            f"Последний пост: {source['last_post_id']}\n"
            f"Проверен: {source.get('last_checked_at', 'никогда')}\n"
            f"Ошибка: {source.get('error_message', 'нет')}"
        )

    def _cmd_admin_reports(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        # Optional: filter by item_id
        item_id = None
        rest = args.strip()
        if rest:
            try:
                item_id = int(rest)
            except ValueError:
                pass
        rows = self._moderation.get_reports(catalog_item_id=item_id, limit=20)
        if not rows:
            return "Жалоб нет."
        lines = [f"Жалобы (последние {len(rows)}):", ""]
        for r in rows:
            item_info = self._catalog.get_item(r["catalog_item_id"])
            title = item_info["title"] if item_info else f"#{r['catalog_item_id']}"
            lines.append(
                f"#{r['id']} на «{title}» (карточка #{r['catalog_item_id']})\n"
                f"  Причина: {r['reason']}\n"
                f"  От: {r['reporter'] or 'аноним'}\n"
                f"  Когда: {r['created_at']}\n"
                f"  /admin_hide_{r['catalog_item_id']} — скрыть"
                + (
                    f"\n  /admin_dismiss_report_{r['id']} — деактивировать"
                    if r['status'] == 'new'
                    else f"\n  Статус: {r['status']}"
                )
            )
        return "\n".join(lines)

    def _cmd_admin_dismiss_report(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        try:
            report_id = int(args.strip())
        except ValueError:
            return "Укажите ID жалобы: /admin_dismiss_report 5"
        self._moderation.dismiss_report(report_id)
        return fmt.format_success(f"Жалоба #{report_id} деактивирована.")

    def _cmd_admin_clear_reports(self, user_id: str, args: str) -> str:
        if not self._is_admin(user_id):
            return fmt.format_error("Нет прав администратора.")
        args = args.strip()
        if not args:
            return (
                "Укажите:\n"
                "/admin_clear_reports 5 — очистить жалобы для карточки #5\n"
                "/admin_clear_reports user@email.com — очистить жалобы от пользователя"
            )
        # Try as item_id first
        try:
            item_id = int(args)
            count = self._moderation.dismiss_reports_for_item(item_id)
            return fmt.format_success(
                f"Деактивировано {count} жалоб для карточки #{item_id}."
            )
        except ValueError:
            pass
        # Try as email
        count = self._moderation.dismiss_reports_by_user_email(args)
        if count:
            return fmt.format_success(
                f"Деактивировано {count} жалоб от {args}."
            )
        return fmt.format_error(f"Пользователь {args} не найден.")

    def _set_card_contact(self, user_id: str, args: str, command_name: str = "/set_contact") -> str:
        """Set admin contact for a catalog item. Only admins or the card owner can change it."""
        if not args.strip():
            return (
                f"Укажите ID карточки и ссылку:\n"
                f"{command_name}_27 https://i.delta.chat/#..."
            )
        parts = args.split(maxsplit=1)
        try:
            item_id = int(parts[0])
        except ValueError:
            return f"Укажите ID карточки:\n{command_name}_27 https://i.delta.chat/#..."

        if len(parts) < 2:
            return (
                f"Укажите ссылку после ID:\n"
                f"{command_name}_{item_id} https://i.delta.chat/#..."
            )

        admin_contact = parts[1]
        if not admin_contact.startswith("https://i.delta.chat/#"):
            return (
                "❌ Контакт должен быть ссылкой-приглашением Delta Chat, "
                "например:\n"
                "https://i.delta.chat/#396AEA25ED9A9EA48CDA8A6011C844075E9791B6&v=3&..."
            )

        item = self._catalog.get_item(item_id)
        if not item:
            return f"❌ Карточка #{item_id} не найдена."

        if not self._is_admin(user_id):
            db_user_id = int(self._resolve_user_id(user_id))
            owner_id = item.get("created_by_user_id")
            if owner_id is None or int(owner_id) != db_user_id:
                return "❌ Можно менять контакт только у своей карточки."

        self._catalog.update_admin_contact(item_id, admin_contact)
        return f"✅ Контакт для карточки #{item_id} сохранён."

    def _cmd_admin_contact(self, user_id: str, args: str) -> str:
        """Admin alias for setting card contact."""
        return self._set_card_contact(user_id, args, "/admin_contact")

    def _cmd_set_contact(self, user_id: str, args: str) -> str:
        """Set admin/contact link for user's own card."""
        return self._set_card_contact(user_id, args, "/set_contact")

    def _cmd_report(self, user_id: str, args: str) -> str:
        match = re.match(r"(\d+)\s+(.+)", args)
        if not match:
            return "Укажите ID и причину:\n/report_123 спам"

        item_id = int(match.group(1))
        reason = match.group(2).strip()

        # Check if item exists
        item = self._catalog.get_item(item_id)
        if not item:
            return f"❌ Карточка #{item_id} не найдена."

        # Check rate limit: 1 report per day total
        limit_msg = self._moderation.can_report(user_id)
        if limit_msg:
            return fmt.format_error(limit_msg)

        result = self._moderation.create_report(
            catalog_item_id=item_id,
            reporter_user_id=user_id,
            reason=reason,
        )

        response = fmt.format_report_confirmed(result["report_id"])
        response += f"\n\n👤 Уникальных жалоб: {result['unique_count']}/{self._moderation.hide_threshold}"

        if result["auto_hidden"]:
            response += "\n\n🚫 Карточка скрыта для проверки."

            # Notify admin
            self._notify_admin_auto_hide(item_id, reason, result["unique_count"])

        return response

    def _notify_admin_auto_hide(self, item_id: int, reason: str, count: int) -> None:
        """Notify admin about auto-hide via a direct message."""
        from ..config import get_config
        cfg = get_config()
        admin_emails = cfg.BOT_ADMIN_EMAILS
        if not admin_emails:
            return
        item = self._catalog.get_item(item_id)
        title = item["title"] if item else f"#{item_id}"
        msg = (
            f"🚫 Авто-скрытие карточки #{item_id}\n\n"
            f"«{title}»\n"
            f"Причина последней жалобы: {reason}\n"
            f"Уникальных жалоб: {count}/{self._moderation.hide_threshold}\n\n"
            f"/admin_show_{item_id} — восстановить\n"
            f"/admin_delete_{item_id} — удалить\n"
            f"/admin_reports {item_id} — все жалобы"
        )
        # Try to find admin's chat and send notification
        for email in admin_emails:
            try:
                contacts = self._adapter._account.get_contacts(email)
                if contacts:
                    chat = self._adapter._account.create_chat_by_contact_id(contacts[0].id)
                    chat.send_text(msg)
                    self._logger.info("Admin notification sent to %s", email)
            except Exception as e:
                self._logger.debug("Admin notify failed for %s: %s", email, e)

    def _async_enrich_chat(self, url: str, item_id: int, user_id: str, reply_chat_id: Optional[int] = None) -> None:
        """Background task: join group/channel, fetch data, then leave."""
        import threading

        def _worker():
            try:
                info = self._adapter.join_via_link(url)
                title = info.get("name", "")
                desc = info.get("description", "")
                tags = info.get("tags", "")
                chat_type = info.get("type", 0)
                avatar_path = info.get("profile_image")

                # Determine item type from chat type
                # 100 = 1:1 chat (bot), 120 = group, 130 = broadcast (channel)
                detected_type = None
                if chat_type == 130:
                    detected_type = "deltachat_channel"
                elif chat_type == 120:
                    detected_type = "deltachat_group"
                elif chat_type == 100:
                    detected_type = "bot"

                # Copy avatar
                final_avatar = None
                if avatar_path:
                    try:
                        import shutil, os
                        dest_dir = os.path.join(
                            os.path.dirname(self._storage.path), "avatars", "dc_channels"
                        )
                        os.makedirs(dest_dir, exist_ok=True)
                        ext = os.path.splitext(avatar_path)[1] or ".jpg"
                        dest = os.path.join(dest_dir, f"chat_{item_id}{ext}")
                        shutil.copy2(avatar_path, dest)
                        final_avatar = dest
                    except Exception:
                        pass

                if detected_type:
                    self._storage.execute(
                        """UPDATE catalog_items
                           SET title=?, description=?, tags=?,
                               avatar_file_path=?, avatar_status=?,
                               type=?, updated_at=?
                           WHERE id=?""",
                        (
                            title or "Без названия",
                            desc,
                            tags,
                            final_avatar,
                            "ok" if final_avatar else "none",
                            detected_type,
                            _now(),
                            item_id,
                        ),
                    )
                else:
                    self._storage.execute(
                        """UPDATE catalog_items
                           SET title=?, description=?, tags=?,
                               avatar_file_path=?, avatar_status=?, updated_at=?
                           WHERE id=?""",
                        (
                            title or "Без названия",
                            desc,
                            tags,
                            final_avatar,
                            "ok" if final_avatar else "none",
                            _now(),
                            item_id,
                        ),
                    )
                # If this was auto-detected as a channel, send follow-up
                if detected_type == "deltachat_channel" and reply_chat_id:
                    try:
                        msg = (
                            f"✅ Карточка #{item_id} определена как **канал**.\n"
                            f"Чтобы добавить контакт администратора, отправьте:\n"
                            f"/admin_contact_{item_id} https://i.delta.chat/#..."
                        )
                        self._adapter.send_message(reply_chat_id, msg)
                    except Exception:
                        pass

                self._logger.info(
                    "Async enrich complete for #%s%s",
                    item_id,
                    f" (detected: {detected_type})" if detected_type else "",
                )
            except Exception as e:
                self._logger.debug("Async enrich failed for #%s: %s", item_id, e)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _cmd_my(self, user_id: str, args: str) -> str:
        db_user_id = self._resolve_user_id(user_id)
        items = self._catalog.get_user_items(str(db_user_id))
        if not items:
            return "У вас пока нет карточек."
        return fmt.format_search_results(items, len(items))

    def _cmd_delete(self, user_id: str, args: str) -> str:
        try:
            item_id = int(args.strip())
        except (ValueError, IndexError):
            return "Укажите ID карточки:\n/delete 184"

        # Resolve email to internal user ID for ownership check
        db_user_id = int(self._resolve_user_id(user_id))

        # Check if user already has a pending delete for this item
        if user_id in self._pending and self._pending[user_id] == {
            "action": "delete", "item_id": item_id, "db_user_id": db_user_id,
        }:
            # Repeat command = confirm delete
            self._pending.pop(user_id)
            success = self._catalog.delete_item(item_id, db_user_id)
            if success:
                return fmt.format_success(f"Карточка #{item_id} удалена.")
            return fmt.format_error("Карточка не найдена.")

        # Get item info for first-time request
        item = self._catalog.get_item(item_id)
        if not item:
            return fmt.format_error("Карточка не найдена.")
        if item.get("created_by_user_id") != db_user_id:
            return fmt.format_error("Нет прав на удаление этой карточки.")

        # Store pending confirmation
        self._pending[user_id] = {
            "action": "delete", "item_id": item_id, "db_user_id": db_user_id,
        }

        # Show card info without invite link
        type_label = {
            "deltachat_group": "группа",
            "deltachat_channel": "канал",
            "telegram_mirror": "TG-зеркало",
            "bot": "бот",
        }.get(item.get("type", ""), item.get("type", "карточка"))

        lines = [
            f"❗️ Вы хотите удалить {type_label} #{item_id}:",
            f"«{item['title']}»",
        ]
        if item.get("description"):
            lines.append(item["description"])
        lines.append("")
        lines.append("Если вы всё равно хотите удалить карточку,")
        lines.append(f"отправьте команду /delete_{item_id} ещё раз.")
        return "\n".join(lines)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
