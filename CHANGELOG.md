# Changelog

## Release 1.0.0 — 2026-07-13

First public release of DC Atlas — a Delta Chat catalog bot.

### Features

- **Automatic card creation** — send any supported link to the bot in 1:1 chat
  - `https://t.me/...` → Telegram mirror channel
  - `https://i.delta.chat/#...` → Delta Chat group, channel or bot
- **Telegram mirrors** — periodic polling, automatic publishing to DC channel
- **Catalog** — search, paginated list, open/delete cards
- **Reports & moderation** — users can report cards, admins can hide/delete
- **Admin commands** — statistics, source management, proxy config
- **Auto-detect only** — no explicit add commands needed, links are detected automatically
- **Support contact** — optional support link shown in welcome message and help
- **Bot avatar** — set automatically on install
- **Rotating logs** — 5 MB per file, 3 backups
- **Welcome on first contact** — sent on first user message in 1:1 chat
- **Smart command parsing** — supports both `/open_5` and `/open 5`
- **Rate limiter** — spam protection for all commands

### Setup

One-command installation via `curl | bash`:
- Creates bot account on chatmail server via `/new` API
- Installs Python dependencies
- Initializes SQLite database
- Creates systemd service with `Restart=always`
- Generates `.env` with validated user inputs
- Displays bot invite link at the end

### Tech

- Python 3.11+, deltachat-rpc-client 2.53+
- Chatmail-compatible (Delta Chat Bot API)
- Works with any chatmail relay
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
