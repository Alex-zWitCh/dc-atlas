"""
Message formatting utilities for DC Atlas responses.

All responses should be plain text, readable without HTML/Markdown.
"""

from typing import Optional

MAX_RESULTS = 5


def _support_line() -> str:
    """Return support contact line if configured."""
    try:
        from ..config import get_config
        url = get_config().SUPPORT_INVITE_URL
    except Exception:
        url = ""
    return f"\n💬 Саппорт: {url}" if url else ""


def format_search_results(items: list[dict], total: int) -> str:
    """Format search results as a compact list."""
    if not items:
        return "Ничего не найдено."

    lines = [f"Найдено: {total}"]
    lines.append("")

    for item in items[:MAX_RESULTS]:
        type_label = {
            "deltachat_group": "группа",
            "deltachat_channel": "канал",
            "telegram_mirror": "TG-зеркало",
            "bot": "бот",
        }.get(item["type"], item["type"])

        lines.append(f"#{item['id']} {item['title']}")
        lines.append(f"  Тип: {type_label}")

        if item.get("tags") and item.get("type") != "telegram_mirror":
            lines.append(f"  Теги: {item['tags']}")

        join_mode = {
            "open": "свободный",
            "contact_author": "через автора",
            "request_external": "по заявке",
            "hidden": "скрыт",
        }.get(item.get("join_mode", ""), item.get("join_mode", ""))

        if join_mode:
            lines.append(f"  Вход: {join_mode}")

        lines.append(f"  /open_{item['id']}  — открыть")
        lines.append("")

    if total > MAX_RESULTS:
        lines.append(f"и ещё {total - MAX_RESULTS}...")

    return "\n".join(lines)


def format_item_card(item: dict) -> str:
    """Format a single catalog item as a full card."""

    type_label = {
        "deltachat_group": "группа Delta Chat",
        "deltachat_channel": "канал Delta Chat",
        "telegram_mirror": "Telegram-зеркало",
        "bot": "бот",
    }.get(item["type"], item["type"])

    lines = [f"#{item['id']} {item['title']}"]
    lines.append(f"Тип: {type_label}")

    if item.get("description"):
        lines.append(f"Описание: {item['description']}")

    # Don't show tags for telegram mirrors
    if item.get("tags") and item.get("type") != "telegram_mirror":
        lines.append(f"Теги: {item['tags']}")

    if item.get("invite_url"):
        lines.append("")
        lines.append("Invite:")
        lines.append(item["invite_url"])

    # Optional contacts
    optional_fields = [
        ("author_contact", "Связь с автором"),
        ("admin_contact", "Администратор"),
        ("proposal_contact", "Предложить пост"),
        ("proposal_group_invite", "Группа предложений"),
    ]
    for field, label in optional_fields:
        if item.get(field):
            lines.append(f"{label}: {item[field]}")

    if item.get("proposal_instruction"):
        lines.append("")
        lines.append(f"Как предложить пост: {item['proposal_instruction']}")

    lines.append("")
    lines.append(f"Пожаловаться: /report_{item['id']}")

    return "\n".join(lines)


def format_welcome() -> str:
    """Welcome message sent when a user first contacts the bot."""
    return f"""Привет! 👋 Я DC Atlas — бот-каталог сообществ Delta Chat.

Я умею автоматически распознавать ссылки:
• Ссылка на Telegram-канал → зеркало
• Ссылка-контакт Delta Chat → бот
• Ссылка-приглашение Delta Chat → группа или канал

Просто отправь мне любую ссылку, и я создам карточку!

Команды:
  /list       — все карточки каталога
  /new        — последние добавленные
  /search     — поиск по каталогу
  /help       — полный список команд{_support_line()}"""


def format_list(result: dict) -> str:
    """Format paginated list of all catalog items grouped by type."""
    items = result["items"]
    total = result["total"]
    page = result["page"]
    pages = result["pages"]
    groups = result["groups"]

    lines = [f"📋 Каталог ({total} карточек)"]

    # Show group summary
    type_labels = {
        "deltachat_group": "групп",
        "deltachat_channel": "каналов",
        "telegram_mirror": "TG-зеркал",
        "bot": "ботов",
    }
    summary = []
    for g in groups:
        label = type_labels.get(g["type"], g["type"])
        summary.append(f"{g['cnt']} {label}")
    if summary:
        lines.append("  " + ", ".join(summary))

    lines.append("")
    for item in items:
        icons = {
            "deltachat_group": "📢",
            "deltachat_channel": "📣",
            "telegram_mirror": "🔄",
            "bot": "🤖",
        }
        icon = icons.get(item["type"], "📌")
        lines.append(f"{icon} #{item['id']} {item['title']}")
        lines.append(f"   /open_{item['id']}")

    # Pagination footer
    if pages > 1:
        lines.append("")
        nav = []
        if page > 1:
            nav.append(f"/list_{page - 1} — ← назад")
        if page < pages:
            nav.append(f"/list_{page + 1} — дальше →")
        lines.append(f"Страница {page}/{pages}")
        if nav:
            lines.append("  ".join(nav))

    return "\n".join(lines)


def format_help(is_admin: bool = False) -> str:
    text = """DC Atlas — каталог сообществ Delta Chat

Команды:
  /help                     — эта справка
  /search <запрос>          — поиск по каталогу
  /list                     — все карточки (с группировкой)
  /list_<N>                 — страница N (по 20 на странице)
  /invite                   — моя инвайт-ссылка (QR-код)
  /new                      — последние добавленные
  /open_<id>               — открыть карточку
  /report_<id> <причина>    — пожаловаться на карточку
  /my                       — мои карточки
  /delete_<id>              — удалить мою карточку
  /set_contact_<id> <invite> — указать контакт для моей карточки

💡 Карточки добавляются автоматически — просто отправьте ссылку:
  • Ссылка-приглашение Delta Chat (группа, канал или бот)
  • Ссылка на Telegram-канал (t.me/... или telegram.me/...)
"""
    if is_admin:
        text += """
🔧 Админ-команды:
  /admin_stats              — статистика бота
  /admin_proxy [on <url>|off] — настроить прокси
  /admin_hide_<id>          — скрыть карточку
  /admin_show_<id>          — восстановить карточку
  /admin_delete_<id>        — удалить карточку (soft)
  /admin_delete_full_<id>   — полностью удалить карточку (hard)
  /admin_contact_<id> <invite> — указать контакт администратора
  /admin_sources            — список TG-источников
  /admin_pause_tg <username> — приостановить зеркало
  /admin_resume_tg <username> — возобновить зеркало
  /admin_check_tg <username> — проверить зеркало
  /admin_reports            — список жалоб
  /admin_reports <id>       — жалобы на конкретную карточку
  /admin_dismiss_report_<id> — деактивировать жалобу
  /admin_clear_reports <id>  — очистить жалобы для карточки
  /admin_clear_reports <email> — очистить жалобы пользователя
"""
    text += _support_line()
    return text


def format_item_added(item_id: int, item_type: str) -> str:
    type_label = {
        "deltachat_group": "группа",
        "deltachat_channel": "канал",
        "telegram_mirror": "Telegram-зеркало",
        "bot": "бот",
    }.get(item_type, item_type)

    return f"✅ Карточка #{item_id} ({type_label}) создана.\nОткрыть: /open_{item_id}"


def format_new_items(items: list[dict]) -> str:
    """Format list of newest items."""
    if not items:
        return "Новых карточек пока нет."

    lines = ["--- Новые карточки ---", ""]
    for item in items:
        type_label = {
            "deltachat_group": "📢",
            "deltachat_channel": "📣",
            "telegram_mirror": "🔄",
            "bot": "🤖",
        }.get(item["type"], "📌")
        lines.append(f"{type_label} #{item['id']} {item['title']}")
        lines.append(f"   /open_{item['id']}")

    lines.append("")
    lines.append("Чтобы открыть, нажмите на команду в списке выше.")

    return "\n".join(lines)


def format_report_confirmed(report_id: int) -> str:
    return f"✅ Жалоба #{report_id} принята. Спасибо, мы проверим."


def format_error(message: str) -> str:
    return f"❌ {message}"


def format_success(message: str) -> str:
    return f"✅ {message}"
