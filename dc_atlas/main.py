"""
DC Atlas — lightweight Delta Chat catalog bot.

Entry point for the application.
Supports --check-config, --init-db and normal runtime modes.
"""

import argparse
import sys
from pathlib import Path

import time

from .config import get_config
from .logging_config import setup_logging
from .services.poller import Poller
from .telegram.public_parser import TelegramPublicParser

logger = setup_logging()


def _mask_sensitive_url(url: str) -> str:
    """Mask password in URLs like http://user:pass@host."""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url or "")


def check_config() -> bool:
    """Validate configuration without starting the bot."""
    try:
        cfg = get_config()
        print(f"config ok: {cfg}")
        print(f"  APP_DATA_DIR:      {cfg.APP_DATA_DIR}")
        print(f"  APP_DB_PATH:       {cfg.APP_DB_PATH}")
        print(f"  APP_LOG_DIR:       {cfg.APP_LOG_DIR}")
        print(f"  BOT_DISPLAY_NAME:  {cfg.BOT_DISPLAY_NAME}")
        print(f"  POLL_INTERVAL:     {cfg.POLL_INTERVAL_SECONDS}s")
        print(f"  TELEGRAM_PROXY:   {'ON' if cfg.TELEGRAM_PROXY_ENABLED else 'OFF'}")
        if cfg.TELEGRAM_PROXY_ENABLED:
            print(f"  PROXY_URL:        {_mask_sensitive_url(cfg.TELEGRAM_PROXY_URL)}")
        dc_status = cfg.DC_EMAIL or "not configured"
        print(f"  DC_ACCOUNT:       {dc_status}")
        print(f"  CATALOG_AUTO_APPROVE: {cfg.CATALOG_AUTO_APPROVE}")
        return True
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return False


def _clean_stale_rpc_lock(profile_path: Path) -> None:
    """Handle stale Delta Chat RPC lock file conservatively.

    Never kill all deltachat-rpc-server processes globally: the server may run
    other Delta Chat bots or clients. Only remove the lock if it looks old.
    """
    lock_file = profile_path / "accounts.lock"
    if not lock_file.exists():
        return

    try:
        age_seconds = time.time() - lock_file.stat().st_mtime
    except OSError as e:
        raise RuntimeError(f"Could not inspect Delta Chat lock file: {lock_file}: {e}")

    if age_seconds > 3600:
        try:
            lock_file.unlink(missing_ok=True)
            logger.warning("Removed old Delta Chat accounts.lock older than 1 hour: %s", lock_file)
            return
        except OSError as e:
            raise RuntimeError(f"Could not remove stale lock file {lock_file}: {e}")

    raise RuntimeError(
        f"Delta Chat profile appears to be locked: {lock_file}. "
        "Stop the existing dc-atlas/deltachat-rpc-server process or remove "
        "the stale lock manually after checking that no process is using it."
    )


def init_db(db_path: Path) -> None:
    """Initialize database with migrations."""
    from .storage.migrations import run_migrations
    from .storage.sqlite_storage import SQLiteStorage

    storage = SQLiteStorage(str(db_path))
    storage.connect()
    run_migrations(storage)
    storage.disconnect()
    print(f"Database initialized at {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DC Atlas Bot")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database and exit",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Database path (overrides config for --init-db)",
    )

    args = parser.parse_args()

    if args.check_config:
        sys.exit(0 if check_config() else 1)

    cfg = get_config()

    if args.init_db:
        db_path = Path(args.db) if args.db else cfg.APP_DB_PATH
        init_db(db_path)
        sys.exit(0)

    # Normal mode — start bot
    logger.info("DC Atlas starting...")

    try:
        from .storage.sqlite_storage import SQLiteStorage
        from .storage.migrations import run_migrations
        from .services.catalog_service import CatalogService
        from .commands.router import CommandRouter

        storage = SQLiteStorage(str(cfg.APP_DB_PATH))
        storage.connect()
        run_migrations(storage)

        catalog = CatalogService(storage)

        # Clean stale DC RPC lock files before starting
        _clean_stale_rpc_lock(cfg.DELTA_CHAT_PROFILE_PATH)

        # Select adapter based on environment
        if cfg.APP_ENV == "production" and cfg.DC_EMAIL:
            from .adapters.deltachat_adapter import DeltaChatAdapter

            adapter = DeltaChatAdapter(str(cfg.DELTA_CHAT_PROFILE_PATH))
            adapter.configure(
                email=cfg.DC_EMAIL,
                password=cfg.DC_PASSWORD,
                display_name=cfg.BOT_DISPLAY_NAME,
                imap_server=cfg.DC_IMAP_SERVER,
                smtp_server=cfg.DC_SMTP_SERVER,
            )
            # Set bot profile avatar if configured
            if cfg.BOT_AVATAR_PATH and Path(cfg.BOT_AVATAR_PATH).is_file():
                try:
                    adapter.set_account_avatar(str(cfg.BOT_AVATAR_PATH))
                    logger.info("Bot avatar set from %s", cfg.BOT_AVATAR_PATH)
                except Exception as e:
                    logger.warning("Could not set bot avatar: %s", e)
            logger.info("DC Atlas using real Delta Chat adapter: %s", cfg.DC_EMAIL)
        else:
            from .adapters.fake_deltachat_adapter import FakeDeltaChatAdapter

            adapter = FakeDeltaChatAdapter(cfg.BOT_DISPLAY_NAME)
            logger.info("DC Atlas using fake adapter (development)")

        router = CommandRouter(catalog, adapter, storage)

        logger.info("DC Atlas ready")
        # Log invite link for setup.sh / admin convenience
        try:
            invite = adapter.get_bot_invite()
            if invite:
                logger.info("Bot invite: %s", invite)
                print("Bot invite:", invite)
                # Write to known path so setup.sh can read it
                try:
                    Path(cfg.APP_DATA_DIR, "BOT_INVITE.txt").write_text(invite)
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Could not get bot invite: %s", e)
        print("Bot is running. Type commands or Ctrl+C to exit.")

        if cfg.APP_ENV == "production":
            _production_loop(adapter, router, storage)
        else:
            _dev_loop(router)

    except KeyboardInterrupt:
        logger.info("DC Atlas stopped by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)


def _production_loop(adapter: any, router: "CommandRouter", storage) -> None:
    """Production event loop with Delta Chat adapter."""
    from .commands import formatters as fmt

    try:
        adapter.start()
    except Exception as e:
        logger.error("Failed to start DC adapter: %s", e)
        logger.info("Continuing without DC adapter — incoming messages will not be processed")

    poller = Poller(storage, adapter, TelegramPublicParser())
    poll_interval = get_config().POLL_INTERVAL_SECONDS

    logger.info("Starting main loop (poll every %ds)", poll_interval)

    # Track users who already received welcome
    welcomed_users = set()

    last_poll = 0
    last_force_check = 0
    last_heartbeat = 0
    last_cleanup = 0
    force_check_interval = 5
    heartbeat_interval = 300

    while True:
        try:
            now = time.time()

            # Force DC core to scan for new messages (bypasses event system)
            try:
                if now - last_force_check >= force_check_interval:
                    adapter.force_check_incoming()
                    last_force_check = now
            except (AttributeError, NotImplementedError):
                pass
            except Exception as e:
                logger.debug("Force check error: %s", e)

            # Heartbeat log
            if now - last_heartbeat >= heartbeat_interval:
                logger.info("Heartbeat OK — bot alive, polling active")
                last_heartbeat = now

            # Check for incoming messages (best-effort) via event queue
            try:
                msg = adapter.wait_for_message(timeout=0.5)
                if msg:
                    text = msg.get("text", "") or ""
                    sender = msg.get("sender_addr", "unknown")
                    chat_id = msg.get("chat_id")
                    is_new_contact = msg.get("is_new_contact", False)
                    is_direct_chat = msg.get("is_direct_chat", True)

                    # Skip empty messages and messages from the bot itself,
                    # EXCEPT new-contact signals (SecureJoin completed)
                    if not is_new_contact:
                        if not text.strip() or not sender:
                            continue
                        if sender == get_config().DC_EMAIL:
                            continue

                    # Send welcome on first message from user in 1:1 chat
                    if is_direct_chat and sender not in welcomed_users:
                        welcomed_users.add(sender)
                        welcome = fmt.format_welcome()
                        if welcome:
                            adapter.send_message(chat_id, welcome)
                            logger.info("Welcome sent to %s (first msg)", sender)
                        # Don't also process this message through router — it would
                        # send a second welcome. The user's message is the trigger.
                        continue

                    # Process all messages — router handles commands and auto-detect
                    if is_new_contact and is_direct_chat:
                        # New contact via SecureJoin — welcome already sent above
                        pass
                    else:
                        response = router.handle(sender, text, chat_id=chat_id, is_direct_chat=is_direct_chat)
                        if response:
                            adapter.send_message(chat_id, response)
                    # Check if admin requested a restart (e.g. proxy change)
                    if getattr(get_config(), "_pending_restart", False):
                        logger.info("Restart requested by admin, exiting...")
                        sys.exit(0)
            except (AttributeError, NotImplementedError):
                pass
            except Exception as e:
                logger.debug("Message check error: %s", e)

            # Poll Telegram sources periodically
            if now - last_poll >= poll_interval:
                result = poller.run_once()
                if result.published > 0 or result.errors > 0:
                    logger.info(
                        "Poll: %d checked, %d published, %d errors",
                        result.checked,
                        result.published,
                        result.errors,
                    )
                last_poll = now

            # Cleanup old messages from DC profile periodically
            if get_config().DC_PROFILE_CLEANUP_ENABLED and now - last_cleanup >= get_config().DC_PROFILE_CLEANUP_INTERVAL_SECONDS:
                try:
                    deleted = adapter.cleanup_old_messages(
                        max_age_days=get_config().DC_PROFILE_CLEANUP_DAYS
                    )
                    if deleted > 0:
                        logger.info("Cleanup: deleted %d old messages", deleted)
                except (AttributeError, NotImplementedError):
                    pass
                except Exception as e:
                    logger.debug("Cleanup error: %s", e)
                last_cleanup = now
            else:
                time.sleep(1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception("Loop error: %s", e)
            time.sleep(10)

    try:
        adapter.stop()
    except Exception:
        pass


def _dev_loop(router: "CommandRouter") -> None:
    """Simple stdin-based loop for development and testing."""
    print("\n--- Dev loop (type /help for commands) ---")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("exit", "quit"):
            break
        response = router.handle("dev_user", line)
        if response:
            print(response)


if __name__ == "__main__":
    main()
