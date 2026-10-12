import os
from datetime import timedelta
from threexui import Panel

XUI_URL = os.getenv("XUI_URL")
XUI_TOKEN = os.getenv("XUI_TOKEN")
XUI_USER = os.getenv("XUI_USER")
XUI_PASS = os.getenv("XUI_PASS")
XUI_INBOUND_ID = int(os.getenv("XUI_INBOUND_ID", "1"))
XUI_TOTAL_GB = int(os.getenv("XUI_TOTAL_GB", "0"))

_panel = None


def get_panel():
    global _panel
    if _panel is None:
        if XUI_TOKEN:
            _panel = Panel(XUI_URL, token=XUI_TOKEN)
        else:
            _panel = Panel(XUI_URL)
            _panel.login(XUI_USER, XUI_PASS)
    return _panel


def create_vpn_client(user_id: int, days: int, limit_ip: int, is_trial: bool = False):
    panel = get_panel()
    email = f"tg_{user_id}"

    if is_trial:
        expires = timedelta(days=-days)
    else:
        expires = timedelta(days=days)

    try:
        existing = panel.clients.get(email)
        if existing:
            panel.clients.update(
                email,
                expires=expires,
                limit_ip=limit_ip
            )
            links = panel.clients.links(email)
            return email, links[0] if links else None
    except Exception:
        pass

    panel.clients.add(
        email=email,
        inbound_ids=[XUI_INBOUND_ID],
        total_gb=XUI_TOTAL_GB,
        expires=expires,
        limit_ip=limit_ip,
    )

    links = panel.clients.links(email)
    return email, links[0] if links else None
