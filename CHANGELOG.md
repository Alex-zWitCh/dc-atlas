# Changelog

## Release 1.1.0 — 2026-07-10

Cumulative release consolidating fixes from 1.0.3 through 1.0.13.

### Security
- Removed global `kill -9` of all `deltachat-rpc-server` processes on startup
- Proxy URL masked in `--check-config` and `/admin_stats`
- `TELEGRAM_PROXY_URL` default is now empty; validation prevents enabled-but-empty proxy

### Features
- `/set_contact_<id> <invite>` — user command to set contact on own card
- Configurable DC profile cleanup: `DC_PROFILE_CLEANUP_ENABLED/DAYS/INTERVAL_SECONDS`
- `SUPPORT_INVITE_URL` in `.env` (replaces hardcoded support link)

### Fixed
- `NameError: cfg` in production loop (bot was crashing on cleanup)
- `join_via_link` sleeps reduced 11s→6s
- Auto-add links now only in 1:1 chats, not groups/channels
- TG mirrors without DC Channel get `pending_setup`, not `active`
- `last_post_id` only advances after successful publish
- `publish_failed` posts can transition to `published` on retry (UPSERT)
- `consecutive_errors` counter: sources disabled after 5 errors, not instantly
- Temporary media files cleaned up after publishing
- Photo count no longer double-counts in Telegram parser
- Stale fetch errors cleared after successful fetch
- `admin_contact` type mismatch (`str` vs `int`) fixed
- `/report` validates card exists before creating report
- Welcome message only in 1:1 chats (not groups)
- Migration 3 now properly registered in `MIGRATIONS` dict

### Docs
- Privacy wording updated: bot temporarily joins, reads metadata, leaves
- Limitations of version 1.x section (EN+RU)
- `retention.py` filename fixed in README.ru

### Setup
- `setup.sh`: uses `--init-db` entrypoint, hides password, secures `.env` (chmod 640)
- `.env.example` synced with `Config` (avatar, cleanup, proxy params)
- `DC_IMAP_SERVER`/`DC_SMTP_SERVER` from `.env` (no hardcoded `mail.zwitch.ru`)

## Release 1.0.0 — 2026-07-07

First public release of DC Atlas — a Delta Chat bot for community catalog.

### Features
- Community catalog: groups, channels, bots; search by name/description/tags
- Telegram mirrors: read-only mirrors with automatic posting
- HTTP-proxy support for Telegram
- Admin panel, reporting system, invite QR, rate limiter
- Smart command parsing (space and underscore)
- **Smart parsing**: commands support both space and underscore (`/open_5` ↔ `/open 5`)

### Installation

```bash
curl -sSL https://raw.githubusercontent.com/Alex-zWitCh/dc-atlas/main/setup.sh | sudo bash
```

### Technical

- Python 3.11+, deltachat-rpc-client, SQLite
- Chatmail-based account creation via HTTP API
- Systemd service with automatic startup
- Interactive setup script with minimal input
- Full documentation in English and Russian
