#!/usr/bin/env python3
"""Simple HTTP server that displays DC Atlas catalog as an HTML table.

Usage:
  python3 web.py                  # port 9199, all interfaces
  python3 web.py --port 8080      # custom port
  python3 web.py --db /path/to/dc_atlas.sqlite3
  python3 web.py --avatars /var/lib/dc-atlas/avatars
  python3 web.py --invite 'https://i.delta.chat/#...'  # bot invite for QR
"""

import argparse
import html
import json
import os
import sqlite3
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Sort order: groups first, then bots, channels, mirrors
# ---------------------------------------------------------------------------
SECTION_ORDER = [
    ("deltachat_group",  "👥", "Группы"),
    ("group",            "👥", "Группы"),
    ("deltachat_bot",    "🤖", "Боты"),
    ("bot",              "🤖", "Боты"),
    ("deltachat_channel","📢", "Каналы"),
    ("channel",          "📢", "Каналы"),
    ("telegram_mirror",  "📡", "Зеркала Telegram"),
]

def _section_key(type_: str) -> int:
    for i, (t, _icon, _label) in enumerate(SECTION_ORDER):
        if t == type_:
            return i
    return len(SECTION_ORDER)  # unknown types at the end


TYPE_ICONS = {
    "telegram_mirror": "📡",
    "deltachat_group": "👥",
    "deltachat_channel": "📢",
    "deltachat_bot": "🤖",
    "group": "👥",
    "channel": "📢",
    "bot": "🤖",
}

TYPE_LABELS = {
    "telegram_mirror": "TG Mirror",
    "deltachat_group": "Group",
    "deltachat_channel": "Channel",
    "deltachat_bot": "Bot",
    "group": "Group",
    "channel": "Channel",
    "bot": "Bot",
}

TEMPLATE_PATH = Path(__file__).parent / "catalog_template.html"


def load_template() -> str:
    """Load HTML template from file, fall back to compiled default."""
    if TEMPLATE_PATH.is_file():
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    # Fallback: minimal inline template so the server runs without the file
    return """<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><title>DC Atlas — Каталог</title></head>
<body><h1>📋 DC Atlas</h1><p>Всего карточек: {{COUNT}}</p>{{BOT_QR}}{{NAV}}{{SECTIONS}}</body>
</html>"""


def generate_qr_svg(data: str) -> str:
    """Generate an inline QR code PNG as base64 data URI."""
    try:
        import qrcode
        import io
        qr = qrcode.QRCode(
            version=1,
            box_size=10,
            border=2,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        import base64
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f'data:image/png;base64,{b64}'
    except ImportError:
        return ""
    except Exception:
        return ""


def get_items(db_path: str) -> list[dict]:
    """Fetch all active catalog items."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, type, title, description, tags, invite_url, "
        "       avatar_file_path, avatar_status, source_ref, "
        "       created_at, updated_at, status "
        "FROM catalog_items "
        "WHERE status = 'active' "
        "ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _build_item_row(item: dict, avatar_base: str) -> str:
    """Render a single table row for an item."""
    tid = item["id"]
    ttype = item["type"]
    title = item["title"] or "(без названия)"
    desc = (item["description"] or "")[:200]
    if len(item.get("description", "") or "") > 200:
        desc += "…"
    tags = item.get("tags", "") or ""
    invite = item.get("invite_url", "") or ""
    icon = TYPE_ICONS.get(ttype, "📄")
    label = TYPE_LABELS.get(ttype, ttype)
    source = item.get("source_ref", "") or ""

    # Avatar
    avatar_html = ""
    avatar_path = item.get("avatar_file_path")
    if avatar_path and os.path.isfile(avatar_path):
        rel = os.path.relpath(avatar_path, avatar_base) if avatar_base else ""
        if rel and not rel.startswith(".."):
            avatar_html = f'<img src="/avatars/{html.escape(rel)}" class="avatar">'
    if not avatar_html:
        avatar_html = f'<div class="avatar-placeholder">{icon}</div>'

    invite_link = ""
    if invite:
        invite_link = f'<a href="{html.escape(invite)}" class="invite-link" title="Open in Delta Chat">🔗</a>'

    return f"""<tr>
            <td class="col-avatar">{avatar_html}</td>
            <td class="col-id">{tid}</td>
            <td class="col-type"><span class="badge badge-{ttype}">{icon} {html.escape(label)}</span></td>
            <td class="col-title">
                <strong>{html.escape(title)}</strong>
                {f'<br><small>{html.escape(desc)}</small>' if desc else ''}
                {f'<br><small class="source">@{html.escape(source)}</small>' if source else ''}
            </td>
            <td class="col-tags">{' '.join(f'<span class="tag">{html.escape(t)}</span>' for t in tags.split(",") if t.strip())}</td>
            <td class="col-invite">{invite_link}</td>
        </tr>"""


def _group_items(items: list[dict]) -> list[tuple[str, str, str, list[dict]]]:
    """Group items by type and return sorted (section_id, icon, label, items) tuples."""
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["type"], []).append(item)

    # Sort items within each group by title
    for g in groups.values():
        g.sort(key=lambda x: (x.get("title") or "").lower())

    # Build ordered output, merging aliased types (e.g. deltachat_group + group)
    seen: set[str] = set()
    result: list[tuple[str, str, str, list[dict]]] = []
    for type_key, icon, label in SECTION_ORDER:
        if type_key in seen:
            continue
        # Collect all items for this section (don't add type_key to seen yet;
        # let the inner loop find it among groups so primary and aliased keys
        # from the same section label are handled together)
        section_items: list[dict] = []
        for alias_key, alias_icon, alias_label in SECTION_ORDER:
            if alias_key in seen:
                continue
            if alias_label == label and alias_key in groups:
                seen.add(alias_key)
                section_items.extend(groups[alias_key])
        if section_items:
            result.append((type_key, icon, label, section_items))

    # Any unknown types not in SECTION_ORDER
    for type_key, items_list in groups.items():
        if type_key not in seen:
            icon = TYPE_ICONS.get(type_key, "📄")
            label = TYPE_LABELS.get(type_key, type_key)
            result.append((type_key, icon, f"{icon} {label}", items_list))

    return result


def build_page(items: list[dict], avatar_base: str, template: str,
               invite_url: str = "", qr_svg: str = "") -> str:
    """Fill the HTML template with sorted sections and navigation."""
    grouped = _group_items(items)

    # Build navigation bar
    nav_links = ""
    for section_id, icon, label, section_items in grouped:
        nav_links += (
            f'<a href="#sec-{section_id}" class="nav-link">'
            f'{icon} {html.escape(label)}'
            f'<span class="count">{len(section_items)}</span>'
            f'</a>\n    ')

    # Build sections
    sections_html = ""
    for section_id, icon, label, section_items in grouped:
        rows = "\n".join(_build_item_row(it, avatar_base) for it in section_items)
        sections_html += f"""<div class="section" id="sec-{section_id}">
    <div class="section-header">
      <h2>{icon} {html.escape(label)}</h2>
      <span class="count">{len(section_items)}</span>
    </div>
    <table>
      <thead><tr>
        <th></th><th>#</th><th>Тип</th><th>Название</th><th>Теги</th><th></th>
      </tr></thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>"""

    # Build QR invite block HTML
    qr_block = ""
    if invite_url and qr_svg:
        qr_block = f"""<div class="qr-block">
    <img src="{qr_svg}" alt="QR-код приглашения бота">
    <div class="qr-text">
      <strong>🤖 Подключиться к боту</strong>
      <a href="{html.escape(invite_url)}">{html.escape(invite_url)}</a>
      <div class="qr-hint">Отсканируйте QR-код или откройте ссылку в Delta Chat</div>
    </div>
  </div>"""

    return (template
            .replace("{{COUNT}}", str(len(items)))
            .replace("{{BOT_QR}}", qr_block)
            .replace("{{NAV}}", nav_links)
            .replace("{{SECTIONS}}", sections_html))


class CatalogHandler(BaseHTTPRequestHandler):
    """HTTP handler serving catalog HTML page and avatar images."""

    db_path = ""
    avatar_base = ""
    template = ""
    invite_url = ""
    qr_svg = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Avatar files
        if path.startswith("/avatars/"):
            rel = path[len("/avatars/"):]
            # Security: prevent path traversal
            if ".." in rel or rel.startswith("/"):
                self.send_error(403)
                return
            file_path = os.path.join(self.avatar_base, rel)
            if not os.path.isfile(file_path):
                self.send_error(404)
                return
            ext = os.path.splitext(file_path)[1].lower()
            mime = {"": "application/octet-stream"}
            self.send_response(200)
            self.send_header("Content-Type", mime.get(ext, "application/octet-stream"))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
            return

        # JSON output
        if path == "/catalog.json":
            items = get_items(self.db_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"count": len(items), "items": items}, ensure_ascii=False).encode())
            return

        # HTML page
        items = get_items(self.db_path)
        page = build_page(items, self.avatar_base, self.template,
                          self.invite_url, self.qr_svg)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {args[0]} {args[1]} {args[2]}\n")


def main():
    parser = argparse.ArgumentParser(description="DC Atlas Catalog Web Server")
    parser.add_argument("--port", type=int, default=9199, help="Port to listen on (default: 9199)")
    parser.add_argument("--db", default="", help="Path to dc_atlas.sqlite3")
    parser.add_argument("--avatars", default="", help="Path to avatars directory")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--invite", default="", help="Bot invite URL for QR code")
    args = parser.parse_args()

    # Find database
    db_path = args.db
    if not db_path:
        candidates = [
            "/var/lib/dc-atlas/dc_atlas.sqlite3",
            "./dc_atlas.sqlite3",
            "./data/dc_atlas.sqlite3",
        ]
        for p in candidates:
            if os.path.isfile(p):
                db_path = p
                break
    if not db_path or not os.path.isfile(db_path):
        print("Database not found. Use --db to specify path.", file=sys.stderr)
        sys.exit(1)

    avatar_base = args.avatars
    if not avatar_base:
        avatar_base = str(Path(db_path).parent / "avatars")
    if not os.path.isdir(avatar_base):
        avatar_base = ""

    CatalogHandler.db_path = db_path
    CatalogHandler.avatar_base = avatar_base
    CatalogHandler.template = load_template()
    CatalogHandler.invite_url = args.invite
    CatalogHandler.qr_svg = generate_qr_svg(args.invite) if args.invite else ""

    server = HTTPServer((args.bind, args.port), CatalogHandler)
    print(f"Serving catalog at http://{args.bind}:{args.port}")
    print(f"  Database: {db_path}")
    print(f"  Avatars:  {avatar_base or '(not found)'}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
