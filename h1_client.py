import os
import secrets
from typing import Any
from urllib.parse import quote

import aiohttp

H1_PANEL_API_URL = os.getenv("H1_PANEL_API_URL", "").rstrip("/")
H1_PANEL_API_TOKEN = os.getenv("H1_PANEL_API_TOKEN", "")
H1_INBOUND_ID = os.getenv("H1_INBOUND_ID", "ib_4cc5c5fa4a")
H1_API_TIMEOUT_SECONDS = float(os.getenv("H1_API_TIMEOUT_SECONDS", "15"))


class H1APIError(RuntimeError):
    pass


def _check_config() -> None:
    if not H1_PANEL_API_URL:
        raise H1APIError("Не задана переменная H1_PANEL_API_URL")
    if not H1_PANEL_API_TOKEN:
        raise H1APIError("Не задана переменная H1_PANEL_API_TOKEN")


async def _request(
    method: str,
    endpoint: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _check_config()
    headers = {
        "Authorization": f"Bearer {H1_PANEL_API_TOKEN}",
        "Accept": "application/json",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    timeout = aiohttp.ClientTimeout(total=H1_API_TIMEOUT_SECONDS)
    url = f"{H1_PANEL_API_URL}/{endpoint.lstrip('/')}"

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=headers, json=json_body, ssl=True
            ) as response:
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {}

                if response.status >= 400:
                    detail = data.get("error") if isinstance(data, dict) else None
                    raise H1APIError(
                        f"H1 API вернул HTTP {response.status}"
                        + (f": {detail}" if detail else "")
                    )

                if not isinstance(data, dict):
                    raise H1APIError("H1 API вернул неожиданный формат ответа")
                if data.get("ok") is False:
                    raise H1APIError(str(data.get("error") or "Ошибка H1 API"))
                return data
    except H1APIError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise H1APIError(f"Не удалось связаться с H1 API: {type(exc).__name__}") from None


async def get_inbounds() -> list[dict[str, Any]]:
    data = await _request("GET", "/inbounds")
    items = data.get("inbounds", data.get("items", []))
    if not isinstance(items, list):
        raise H1APIError("H1 API вернул некорректный список инбаундов")
    return [item for item in items if isinstance(item, dict)]


async def get_client(name: str) -> dict[str, Any] | None:
    try:
        data = await _request("GET", f"/clients/{quote(name, safe='')}")
    except H1APIError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    client = data.get("client")
    return client if isinstance(client, dict) else data


async def create_vpn_client(
    user_id: int,
    days: int,
    device_limit: int,
    traffic_limit_gb: float = 0,
) -> dict[str, Any]:
    if user_id <= 0:
        raise ValueError("user_id должен быть положительным")
    if days <= 0:
        raise ValueError("days должен быть больше нуля")
    if device_limit not in (3, 5, 7):
        raise ValueError("device_limit должен быть 3, 5 или 7")
    if traffic_limit_gb < 0:
        raise ValueError("traffic_limit_gb не может быть отрицательным")
    if not H1_INBOUND_ID:
        raise H1APIError("Не задана переменная H1_INBOUND_ID")

    name = f"tg_{user_id}"
    existing = await get_client(name)
    if existing is not None:
        raise H1APIError(
            f"Клиент {name} уже существует в H1; проверьте его перед повторной выдачей"
        )

    payload = {
        "name": name,
        "days": days,
        "device_limit": device_limit,
        "traffic_limit_gb": traffic_limit_gb,
        "manual": True,
        "inbound_ids": [H1_INBOUND_ID],
    }
    data = await _request("POST", "/create", json_body=payload)
    client = data.get("client")
    if not isinstance(client, dict):
        raise H1APIError("H1 не вернул объект созданного клиента")
    return client


async def create_test_client(admin_user_id: int) -> dict[str, Any]:
    """Create a uniquely named short-lived test client; caller must delete it."""
    if admin_user_id <= 0:
        raise ValueError("admin_user_id должен быть положительным")
    if not H1_INBOUND_ID:
        raise H1APIError("Не задана переменная H1_INBOUND_ID")
    name = f"bot_test_{admin_user_id}_{secrets.token_hex(4)}"
    payload = {
        "name": name,
        "days": 1,
        "device_limit": 3,
        "traffic_limit_gb": 0,
        "manual": True,
        "inbound_ids": [H1_INBOUND_ID],
    }
    data = await _request("POST", "/create", json_body=payload)
    client = data.get("client")
    if not isinstance(client, dict):
        # Creation may have succeeded even if the response shape was unexpected.
        raise H1APIError(f"H1 не вернул объект тестового клиента; проверьте имя {name}")
    client["name"] = name
    return client


async def delete_client(name: str) -> None:
    if not name or not name.startswith("bot_test_"):
        raise H1APIError("Удаление разрешено только для временных тестовых клиентов")
    await _request("DELETE", f"/clients/{quote(name, safe='')}")
