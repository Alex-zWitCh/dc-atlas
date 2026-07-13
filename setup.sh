#!/usr/bin/env bash
# DC Atlas — one-command setup
# Usage: curl -sSL https://raw.githubusercontent.com/Alex-zWitCh/dc-atlas/main/setup.sh | bash
set -euo pipefail

# When piped (curl | bash), stdin is the pipe with the script.
# We must NOT redirect stdin (bash reads the script from it).
# Instead, open /dev/tty on fd 3 for interactive input.
if [[ ! -t 0 ]]; then
    exec 3< /dev/tty
    read_input() { builtin read -r "$@" <&3; }
else
    read_input() { builtin read -r "$@"; }
fi

# ---------- helpers ----------
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { printf "${CYAN}%s${NC}\n" "$*"; }
ok()    { printf "${GREEN}✓ %s${NC}\n" "$*"; }
warn()  { printf "${RED}• %s${NC}\n" "$*"; }
err()   { echo; warn "$*"; exit 1; }
prompt(){ printf "${CYAN}▶ %s${NC} " "$*"; }

# ---------- root check ----------
if [[ $EUID -ne 0 ]]; then
    err "Запустите скрипт от root: sudo bash setup.sh"
fi

# ---------- repo path ----------
REPO_DIR="/opt/dc-atlas"
if [[ -d "$REPO_DIR/.git" ]]; then
    info "Репозиторий уже существует в $REPO_DIR, обновляем…"
    cd "$REPO_DIR"
    git pull --ff-only
else
    info "Клонирование репозитория…"
    rm -rf "$REPO_DIR"
    git clone https://github.com/Alex-zWitCh/dc-atlas.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# ---------- install system deps ----------
info "Установка системных зависимостей…"
info "  apt-get update (может занять до минуты)…"
apt-get update -q 2>/dev/null || apt-get update
info "  apt-get install python3, pip, git…"
apt-get install -y -qq python3 python3-venv python3-pip curl git

# ---------- collect config ----------
echo ""
info "===== Конфигурация ====="
echo ""

prompt "Домен chatmail-сервера [nine.testrun.org]:"
read_input CHATMAIL_DOMAIN
CHATMAIL_DOMAIN="${CHATMAIL_DOMAIN:-nine.testrun.org}"
# Validate domain: remove protocol prefix, strip whitespace, check format
CHATMAIL_DOMAIN="${CHATMAIL_DOMAIN#https://}"
CHATMAIL_DOMAIN="${CHATMAIL_DOMAIN#http://}"
CHATMAIL_DOMAIN="${CHATMAIL_DOMAIN%%/*}"
CHATMAIL_DOMAIN="$(echo "$CHATMAIL_DOMAIN" | xargs)"
if ! echo "$CHATMAIL_DOMAIN" | grep -qP '^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'; then
    err "Некорректный домен: $CHATMAIL_DOMAIN. Пример: nine.testrun.org"
fi

info "Введите ссылки-приглашения администраторов (по одной или несколько)."
info "Скопируйте из Delta Chat: профиль → Пригласить → скопировать ссылку."
info "После ввода всех ссылок нажмите Enter на пустой строке:"
ADMIN_RAW=""
while true; do
    prompt ">"
    read_input line || break
    # Trim whitespace
    line="${line## }"
    line="${line%% }"
    [[ -z "$line" ]] && break
    ADMIN_RAW="${ADMIN_RAW}${line}"$'\n'
done

# Extract emails from invite links, filter junk
export ADMIN_RAW
ADMIN_EMAILS=$(python3 << 'PYEOF'
import os, re

raw = os.environ.get("ADMIN_RAW", "")
# Find URL-encoded emails from invite links: a=user%40domain
emails = re.findall(r'a=([a-zA-Z0-9._%+-]+%40[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', raw)
emails = [e.replace('%40', '@') for e in emails]
# Fallback: find plain email addresses
if not emails:
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', raw)
if not emails:
    print("", end="")
    exit(1)
# Deduplicate while preserving order
seen = set()
unique = []
for e in emails:
    if e not in seen:
        seen.add(e)
        unique.append(e)
print('\n'.join(unique))
PYEOF
) || err "Не удалось извлечь email из ссылок. Убедитесь, что ссылка содержит a=ваш_email@домен"

ok "Администраторы: $(echo "$ADMIN_EMAILS" | tr '\n' ' ')"

prompt "Использовать HTTP-прокси для Telegram? (y/N): "
read_input USE_PROXY
TELEGRAM_PROXY_ENABLED="false"
TELEGRAM_PROXY_URL=""
if [[ "$USE_PROXY" =~ ^[YyДд] ]]; then
    prompt "URL прокси (http://user:pass@host:port):"
    read_input PROXY_URL
    # Validate proxy URL format
    PROXY_URL="$(echo "$PROXY_URL" | xargs)"
    if ! echo "$PROXY_URL" | grep -qP '^https?://[^:@]+:[^@]+@[^:/]+(:\d+)?$'; then
        err "Некорректный URL прокси. Ожидается: http://user:pass@host:port"
    fi
    TELEGRAM_PROXY_ENABLED="true"
    TELEGRAM_PROXY_URL="${PROXY_URL}"
fi

prompt "Ссылка на саппорт (Enter чтобы пропустить): "
read_input SUPPORT_INVITE_URL
SUPPORT_INVITE_URL="${SUPPORT_INVITE_URL:-}"
if [[ -n "$SUPPORT_INVITE_URL" ]]; then
    SUPPORT_INVITE_URL="$(echo "$SUPPORT_INVITE_URL" | xargs)"
    # Must be a valid Delta Chat invite link or email
    if ! echo "$SUPPORT_INVITE_URL" | grep -qP '^https://i\.delta\.chat/#|^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'; then
        err "Ссылка на саппорт должна быть ссылкой-приглашением Delta Chat (https://i.delta.chat/#...) или email-адресом."
    fi
fi

# ---------- create bot account ----------
echo ""
info "Создание аккаунта на $CHATMAIL_DOMAIN…"
API_URL="https://${CHATMAIL_DOMAIN}/new"
HTTP_RESPONSE=$(curl -sS -X POST -o /tmp/dc_atlas_account.json -w "%{http_code}" "$API_URL" 2>/dev/null || true)
if [[ "$HTTP_RESPONSE" != "200" ]]; then
    err "Не удалось создать аккаунт на $CHATMAIL_DOMAIN (HTTP $HTTP_RESPONSE)."
fi

python3 << 'PYEOF' > /tmp/dc_atlas_vars.sh
import json
with open('/tmp/dc_atlas_account.json') as f:
    data = json.load(f)
import shlex
email = shlex.quote(data['email'])
password = shlex.quote(data['password'])
print(f'DC_EMAIL={email}')
print(f'DC_PASSWORD={password}')
PYEOF
source /tmp/dc_atlas_vars.sh
rm -f /tmp/dc_atlas_account.json /tmp/dc_atlas_vars.sh
[[ -n "${DC_EMAIL:-}" && -n "${DC_PASSWORD:-}" ]] || err "Ошибка получения данных аккаунта."

ok "Аккаунт создан: $DC_EMAIL"

# ---------- create data dirs ----------
APP_DATA_DIR="/var/lib/dc-atlas"
mkdir -p "$APP_DATA_DIR" "$APP_DATA_DIR/deltachat-profile" /var/log/dc-atlas

# ---------- copy bot avatar ----------
BOT_AVATAR_DIR="$APP_DATA_DIR/avatars/defaults"
mkdir -p "$BOT_AVATAR_DIR"
if [[ -f "$REPO_DIR/assets/bot-avatar.png" ]]; then
    cp "$REPO_DIR/assets/bot-avatar.png" "$BOT_AVATAR_DIR/bot.png"
    ok "Аватар бота скопирован"
else
    warn "Файл аватара не найден в $REPO_DIR/assets/bot-avatar.png"
fi

# ---------- create .env ----------
# Use Python + shlex.quote() for shell-safe quoting — the password may
# contain any character (" ' $ \ ` | etc.) and the heredoc approach
# cannot handle that safely.
export APP_DATA_DIR TELEGRAM_PROXY_ENABLED TELEGRAM_PROXY_URL SUPPORT_INVITE_URL
export DC_EMAIL DC_PASSWORD CHATMAIL_DOMAIN REPO_DIR
# Convert newline-separated admin emails to comma-separated for .env
ADMIN_CSV=$(echo "$ADMIN_EMAILS" | tr '\n' ',')
export ADMIN_EMAILS="$ADMIN_CSV"
python3 << 'PYEOF'
import os, re

def _dotenv_val(v: str) -> str:
    """Quote a value for .env — python-dotenv compatible AND shell-safe (source .env)."""
    # No quoting needed for safe values
    if not v or re.match(r'^[a-zA-Z0-9_./@:,%-]+$', v):
        return v
    # Double-quote with minimal escaping: python-dotenv handles " \ and $
    escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'

env = {
    "APP_ENV": "production",
    "APP_DATA_DIR": os.environ["APP_DATA_DIR"],
    "APP_DB_PATH": os.environ["APP_DATA_DIR"] + "/dc_atlas.sqlite3",
    "APP_LOG_DIR": "/var/log/dc-atlas",
    "BOT_DISPLAY_NAME": "DC Atlas",
    "BOT_AVATAR_PATH": os.environ["APP_DATA_DIR"] + "/avatars/defaults/bot.png",
    "BOT_LANGUAGE": "ru",
    "POLL_INTERVAL_SECONDS": "300",
    "POLL_MAX_SOURCES_PER_CYCLE": "100",
    "POLL_HTTP_TIMEOUT_SECONDS": "15",
    "TELEGRAM_PUBLIC_BASE_URL": "https://t.me/s",
    "TELEGRAM_MAX_PHOTOS_PER_POST": "3",
    "TELEGRAM_FETCH_MEDIA": "false",
    "TELEGRAM_TEMP_MEDIA_DIR": "/tmp/dc-atlas-media",
    "TELEGRAM_STORE_MEDIA_BINARY": "false",
    "TELEGRAM_STORE_FULL_TEXT": "true",
    "TELEGRAM_POST_RETENTION_DAYS": "30",
    "TELEGRAM_POST_RETENTION_MAX_PER_SOURCE": "500",
    "TELEGRAM_PROXY_ENABLED": os.environ["TELEGRAM_PROXY_ENABLED"],
    "TELEGRAM_PROXY_URL": os.environ["TELEGRAM_PROXY_URL"],
    "SUPPORT_INVITE_URL": os.environ.get("SUPPORT_INVITE_URL", ""),
    "CATALOG_AUTO_APPROVE": "true",
    "REPORTS_TO_HIDE": "5",
    "DELTA_CHAT_PROFILE_PATH": os.environ["APP_DATA_DIR"] + "/deltachat-profile",
    "DC_EMAIL": os.environ["DC_EMAIL"],
    "DC_PASSWORD": os.environ["DC_PASSWORD"],
    "DC_IMAP_SERVER": os.environ["CHATMAIL_DOMAIN"],
    "DC_SMTP_SERVER": os.environ["CHATMAIL_DOMAIN"],
    "BOT_ADMIN_EMAILS": os.environ["ADMIN_EMAILS"],
}

with open(os.path.join(os.environ["REPO_DIR"], ".env"), "w") as f:
    for k, v in env.items():
        f.write(f"{k}={_dotenv_val(v)}\n")
PYEOF
ok ".env создан"

# ---------- create user ----------
id -u dc-atlas &>/dev/null || useradd -r -s /usr/sbin/nologin -d "$APP_DATA_DIR" dc-atlas

# ---------- install python deps ----------
info "Установка Python-зависимостей…"
cd "$REPO_DIR"
python3 -m venv venv
source venv/bin/activate
info "  pip upgrade…"
pip install -q --upgrade pip
info "  pip install -r requirements.txt (загрузка пакетов)…"
pip install -q -r requirements.txt
ok "Зависимости установлены"

# ---------- link deltachat-rpc-server ----------
if [[ -f "$REPO_DIR/venv/bin/deltachat-rpc-server" ]]; then
    ln -sf "$REPO_DIR/venv/bin/deltachat-rpc-server" /usr/local/bin/deltachat-rpc-server
fi

# ---------- fix permissions ----------
chown -R dc-atlas:dc-atlas "$APP_DATA_DIR" /var/log/dc-atlas "$REPO_DIR"
# REPO_DIR base owned by root:dc-atlas:775 so dc-atlas can write .env
chown root:dc-atlas "$REPO_DIR"
chmod 775 "$REPO_DIR"

# ---------- secure .env ----------
chown root:dc-atlas "$REPO_DIR/.env"
chmod 640 "$REPO_DIR/.env"
ok ".env защищён (chmod 640, владелец root:dc-atlas)"

# ---------- init DB ----------
info "Инициализация базы данных…"
cd "$REPO_DIR"
sudo -u dc-atlas bash -c "
  cd '$REPO_DIR'
  set -a
  source .env
  set +a
  '$REPO_DIR/venv/bin/python' -m dc_atlas.main --init-db
" && ok "База данных инициализирована"

# ---------- setup systemd ----------
info "Настройка systemd…"
cat > /etc/systemd/system/dc-atlas.service << 'SERVICE'
[Unit]
Description=DC Atlas Bot
After=network.target

[Service]
Type=simple
User=dc-atlas
WorkingDirectory=/opt/dc-atlas
EnvironmentFile=/opt/dc-atlas/.env
ExecStart=/opt/dc-atlas/venv/bin/python -m dc_atlas.main
ExecStopPost=/bin/sh -c 'rm -f /var/lib/dc-atlas/deltachat-profile/accounts.lock'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now dc-atlas
ok "systemd сервис запущен"

# ---------- get bot invite from file ----------
info "Ожидание инвайт-ссылки бота…"
BOT_INVITE=""
INVITE_FILE="/var/lib/dc-atlas/BOT_INVITE.txt"
for i in $(seq 1 45); do
    sleep 2
    if [[ -f "$INVITE_FILE" ]]; then
        BOT_INVITE=$(cat "$INVITE_FILE")
        break
    fi
done
# One more check after the loop in case the file was just written
if [[ -z "$BOT_INVITE" && -f "$INVITE_FILE" ]]; then
    BOT_INVITE=$(cat "$INVITE_FILE")
fi
info "Инвайт: ${BOT_INVITE:-не получен}"

# ---------- done ----------
echo ""
info "============================================"
info "  DC Atlas установлен и запущен!"
info "============================================"
echo ""
echo "  Email бота:    $DC_EMAIL"
echo "  Сервер:        $CHATMAIL_DOMAIN"
echo "  Админы:        $ADMIN_EMAILS"
echo "  Пароль:        сохранён в $REPO_DIR/.env"
if [[ -n "${BOT_INVITE:-}" ]]; then
    echo ""
    echo "  🤖 Инвайт бота:"
    echo "  $BOT_INVITE"
fi
echo ""
echo "  Статус: systemctl status dc-atlas"
echo "  Логи:   journalctl -u dc-atlas -f"
echo "  Стоп:   systemctl stop dc-atlas"
echo "  Рестарт:systemctl restart dc-atlas"
echo ""
