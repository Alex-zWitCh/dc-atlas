<img alt="DC Atlas" src="assets/bot-avatar.png" width="100" height="100">

> 🌐 [Русская версия](README.ru.md)

# DC Atlas

**DC Atlas** is an open-source bot for [Delta Chat](https://delta.chat) that works as a community catalog. It helps find groups, channels, bots and Telegram mirrors, and creates read-only mirrors of public Telegram channels right inside the messenger.

---

## Features

### 📋 Community Catalog
- Add **groups**, **channels** and **bots** to a single catalog
- Each card contains: name, description, tags (#hashtags), avatar, invite link
- Search by name, description and tags — `/search <query>`
- Browse new cards (`/new`) and your own cards (`/my`)
- Open card by ID — `/open_5`

### 🔄 Telegram Mirrors
- Create read-only mirrors of public Telegram channels (auto-detect by `https://t.me/...` link)
- Automatic posting of new messages to Delta Chat Channel
- **HTTP-proxy** support for bypassing Telegram blocks
- Mirror management: pause, resume, check
- Automatic cleanup of old posts (by age and count)

### 🛡️ Moderation & Reporting
- Report cards — `/report_<id> <reason>`
- Auto-hide when report limit is exceeded (configurable)
- Admin panel: hide, show, full delete

### 🔐 Privacy
- Works on a **chatmail** server — no SMS, no phone number required
- End-to-end encryption (Autocrypt) for all messages
- No permanent group or channel monitoring
- When a Delta Chat invite is added, the bot temporarily joins the group/channel,
  reads metadata needed for the catalog card — name, description, avatar and hashtags —
  and then leaves
- Telegram mirrors use only public Telegram pages

### 💡 Additional
- `/invite` — generate QR code with bot invite
- `/help` — interactive help with all commands
- Smart command parsing: space **or** underscore (`/open_5` ↔ `/open 5`)
- Rate limiter for spam protection

---

## Commands

### User commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/invite` | QR code with bot invite |
| `/search <query>` | Search catalog |
| `/open_<id>` | Open card |
| `/new` | New cards |
| `/my` | My cards |
| `/delete_<id>` | Delete your card |
| `/set_contact_<id> <invite>` | Set contact link for your card |
| `/report_<id> <reason>` | Report a card |
| `/list [page]` | Paginated card list |

### Auto-add by link

Cards are created automatically — just send a supported link in a direct 1:1 chat:
- `https://t.me/...` or `https://telegram.me/...` → Telegram mirror
- `https://i.delta.chat/#...` → Delta Chat group, channel or bot

**Note**: auto-add works only in direct 1:1 chats, not in groups/channels.

### Admin commands

| Command | Description |
|---------|-------------|
| `/admin_stats` | Bot statistics + disk usage |
| `/admin_hide_<id>` | Hide card |
| `/admin_show_<id>` | Restore card |
| `/admin_delete_<id>` | Soft delete card |
| `/admin_delete_full_<id>` | Full delete with cleanup |
| `/admin_contact_<id> <invite>` | Set card admin contact |
| `/admin_sources` | List Telegram sources |
| `/admin_pause_tg <username>` | Pause mirror |
| `/admin_resume_tg <username>` | Resume mirror |
| `/admin_check_tg <username>` | Check mirror status |
| `/admin_clear_reports <email>` | Clear reports from user |
| `/admin_reports` | View reports |
| `/admin_proxy` | Set/view Telegram proxy (`on url` / `off`) |

---

## One-command install

```bash
curl -sSL https://raw.githubusercontent.com/Alex-zWitCh/dc-atlas/main/setup.sh | sudo bash
```

The script will ask:

1. **Chatmail server domain** (default: `nine.testrun.org`) — bot account is created automatically via `/new`
2. **Administrator Delta Chat invite links** — paste one or more invite links from Delta Chat
3. **HTTP proxy for Telegram** (optional) — to bypass blocking
4. **Support contact link or email** (optional)
5. **Web server port for catalog** (optional, default `9199`) — if set, creates a simple HTTP page to browse catalog
   - Prompts for bind address: `127.0.0.1` (local only, recommended) or `0.0.0.0` (all interfaces — **not secure**)
   - Creates `dc-atlas-web` systemd service alongside the bot

If `/opt/dc-atlas` already exists, the script will ask to delete and reinstall (default: yes).

The installer extracts admin email addresses from the Delta Chat invite links automatically.

Supported links are auto-detected in direct 1:1 chats:
- Telegram channel link (`https://t.me/...`) → Telegram mirror
- Delta Chat invite link (`https://i.delta.chat/#...`) → group, channel or bot

**Important**: auto-add works only in direct 1:1 chats, not in groups/channels.

Everything else is automatic:
- Clones repo to `/opt/dc-atlas`
- Creates bot account via chatmail server API
- Installs Python dependencies
- Initializes the database
- Creates `dc-atlas` system user
- Configures and starts systemd service

After installation the bot is already running:

```bash
systemctl status dc-atlas
journalctl -u dc-atlas -f
```

### Web catalog server (optional)

If a web server port was specified during setup, a second service is also running:

```bash
systemctl status dc-atlas-web
journalctl -u dc-atlas-web -f
```

The catalog page is available at the configured address. To edit the page appearance, modify the template:

```bash
nano /opt/dc-atlas/web/catalog_template.html
```

Then restart the web server:

```bash
systemctl restart dc-atlas-web
```

### Requirements

- **OS**: Debian 12 / Ubuntu 24.04+
- **RAM**: from 512 MB
- **Internet**: outgoing HTTPS (443) and IMAP/SMTP access to chatmail server
- **Access**: root (for package installation and systemd)

---

## Configuration

Most parameters are set automatically by `setup.sh`.
To change manually, edit `/opt/dc-atlas/.env` and restart:

```bash
systemctl restart dc-atlas
```

### Core (set by setup.sh)

| Variable | Default | Description |
|----------|---------|-------------|
| `DC_EMAIL` | — | Bot email (auto-created) |
| `DC_PASSWORD` | — | Bot password (auto-generated) |
| `BOT_ADMIN_EMAILS` | — | Admin emails (set during install) |
| `DC_IMAP_SERVER` | — | IMAP server (chatmail domain) |
| `DC_SMTP_SERVER` | — | SMTP server (chatmail domain) |

### Telegram & proxy (set by setup.sh)

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_PROXY_ENABLED` | `false` | Enable HTTP proxy |
| `TELEGRAM_PROXY_URL` | — | Proxy URL (`http://user:pass@host:port`) |
| `POLL_INTERVAL_SECONDS` | `300` | Telegram poll interval |
| `POLL_MAX_SOURCES_PER_CYCLE` | `100` | Max sources per cycle |
| `POLL_HTTP_TIMEOUT_SECONDS` | `15` | HTTP request timeout |
| `TELEGRAM_FETCH_MEDIA` | `true` | Fetch photos (up to 3 per post) |
| `TELEGRAM_STORE_MEDIA_BINARY` | `true` | Download video and other files |
| `TELEGRAM_POST_RETENTION_DAYS` | `30` | Keep posts N days |
| `TELEGRAM_POST_RETENTION_MAX_PER_SOURCE` | `500` | Max posts per source |

### Catalog & moderation (rarely changed)

| Variable | Default | Description |
|----------|---------|-------------|
| `CATALOG_AUTO_APPROVE` | `true` | Auto-publish cards |
| `REPORTS_TO_HIDE` | `5` | Reports to auto-hide |
| `AVATAR_FETCH_ENABLED` | `true` | Fetch avatars |
| `AVATAR_MAX_BYTES` | `1048576` | Max avatar size |

> Full list — in `.env.example`.

---

## Telegram Mirror: how it works

1. User sends `https://t.me/username` to the bot
2. Bot detects the link and creates a Telegram mirror channel
3. Opens the public Telegram page, extracts title and description
4. New posts are checked at configurable interval (default 5 minutes)
5. Posts are published to the Delta Chat channel

### Proxy for Telegram

Telegram may be blocked in some regions. To bypass:

1. Deploy an HTTP proxy on an external server
2. Configure:
   ```
   TELEGRAM_PROXY_ENABLED=true
   TELEGRAM_PROXY_URL=http://user:pass@proxy.example.com:1080
   ```
3. **Never put your working proxy in public code** — set it only in local `.env`

---

## Card types

| Type | Icon | Description |
|------|------|-------------|
| Group | 📢 | Delta Chat group |
| Channel | 📣 | Delta Chat channel |
| Telegram Mirror | 🔄 | Telegram channel mirror |
| Bot | 🤖 | Delta Chat bot |

---

## Limitations of version 1.x

- Telegram mirrors work only with public Telegram channels.
- Private Telegram channels and invite-only Telegram channels are not supported.
- Telegram parsing is based on the public HTML page `t.me/s/<channel>`.
- If Telegram changes its HTML layout, the parser may require an update.
- Videos, documents and archives are not downloaded or forwarded.
- Photos, videos and files are forwarded by default (`TELEGRAM_FETCH_MEDIA=true`, `TELEGRAM_STORE_MEDIA_BINARY=true`).
- Delta Chat groups listed in the catalog are ordinary groups: all members may write there.
- Read-only behavior is available only for Delta Chat Channels.

## FAQ

**❓ Do I need a chatmail server?**
Yes, the bot needs an account on a chatmail server (or any Delta Chat-compatible server). You can use public servers or set up your own following [chatmail documentation](https://chatmail.at/doc/relay/).

**❓ Can the bot read private messages?**
No. The bot does not read arbitrary private conversations. It processes direct 1:1 messages sent to it.
When a user explicitly submits a Delta Chat group/channel invite, the bot temporarily joins that chat,
reads metadata needed for the catalog card, and then leaves. It does not stay there for permanent monitoring.

**❓ How does encryption work?**
All messages are end-to-end encrypted (Autocrypt). Keys are generated automatically on first login.

**❓ Can I run multiple bots?**
Yes, each needs a separate Delta Chat account, separate data directory and separate process.

**❓ How does old data cleanup work?**
The bot automatically deletes messages older than 7 days from its DC profile every hour. On the server side, chatmail-expire removes emails older than configured days and handles quota limits.

---

## License

[MIT](LICENSE)

---

## Links

- [GitHub Repository](https://github.com/Alex-zWitCh/dc-atlas)
- [Report a bug](https://github.com/Alex-zWitCh/dc-atlas/issues)
