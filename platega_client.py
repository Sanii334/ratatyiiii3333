import os
from typing import Any
from urllib.parse import quote
import aiohttp

BASE_URL = "https://app.platega.io"
MERCHANT_ID = os.getenv("PLATEGA_MERCHANT_ID", "").strip()
SECRET = (os.getenv("PLATEGA_SECRET") or os.getenv("PLATEGA_API_KEY") or "").strip()
RETURN_URL = os.getenv("PLATEGA_RETURN_URL", "https://t.me/vozd1hVPN_bot").strip()
FAILED_URL = os.getenv("PLATEGA_FAILED_URL", "https://t.me/vozd1hVPN_bot").strip()

class PlategaAPIError(RuntimeError):
    pass

def is_configured() -> bool:
    return bool(MERCHANT_ID and SECRET)

async def _request(method: str, endpoint: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if not is_configured():
        raise PlategaAPIError("Не заданы PLATEGA_MERCHANT_ID и PLATEGA_SECRET или PLATEGA_API_KEY")
    headers = {"X-MerchantId": MERCHANT_ID, "X-Secret": SECRET, "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, f"{BASE_URL}/{endpoint.lstrip('/')}", headers=headers, json=body, ssl=True) as response:
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {}
                if response.status >= 400:
                    raise PlategaAPIError(f"Platega API вернул HTTP {response.status}")
                if not isinstance(data, dict):
                    raise PlategaAPIError("Platega API вернул неожиданный формат ответа")
                return data
    except PlategaAPIError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PlategaAPIError(f"Не удалось связаться с Platega: {type(exc).__name__}") from None

async def create_payment(*, user_id: int, username: str | None, amount: int, devices: int, days: int) -> dict[str, str]:
    if user_id <= 0 or amount <= 0 or days <= 0 or devices not in (3, 5, 7):
        raise ValueError("Некорректные параметры платежа")
    body = {
        "paymentDetails": {"amount": amount, "currency": "RUB"},
        "description": f"Воздух VPN: {devices} устройств на {days} дней",
        "return": RETURN_URL,
        "failedUrl": FAILED_URL,
        "payload": f"vozduh:{user_id}:{devices}:{days}",
        "metadata": {"userId": str(user_id), "userName": (username or f"user_{user_id}")[:64]},
    }
    data = await _request("POST", "/v2/transaction/process", body)
    transaction_id = data.get("transactionId") or data.get("id")
    payment_url = data.get("url") or data.get("redirect")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise PlategaAPIError("Platega не вернул ID транзакции")
    if not isinstance(payment_url, str) or not payment_url.startswith("https://"):
        raise PlategaAPIError("Platega не вернул HTTPS-ссылку оплаты")
    return {"transaction_id": transaction_id, "payment_url": payment_url}

async def get_transaction(transaction_id: str) -> dict[str, Any]:
    return await _request("GET", f"/transaction/{quote(transaction_id, safe='')}")
