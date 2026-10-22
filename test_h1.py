import asyncio
import os
import time
from urllib.parse import quote

from h1_client import H1_INBOUND_ID, H1APIError, _request, get_client, get_inbounds


async def main():
    if not H1_INBOUND_ID:
        raise SystemExit("ERROR: H1_INBOUND_ID не задан")

    inbounds = await get_inbounds()
    inbound_ids = {
        str(item.get("id", item.get("inbound_id", "")))
        for item in inbounds
    }
    if H1_INBOUND_ID not in inbound_ids:
        raise SystemExit("ERROR: H1_INBOUND_ID не найден в списке H1 inbounds")

    name = f"test_bot_check_{int(time.time())}"
    created = False

    try:
        result = await _request(
            "POST",
            "/create",
            json_body={
                "name": name,
                "days": 1,
                "device_limit": 3,
                "traffic_limit_gb": 0,
                "manual": True,
                "inbound_ids": [H1_INBOUND_ID],
            },
        )
        created = True
        client = result.get("client")
        if not isinstance(client, dict):
            raise H1APIError("H1 не вернул объект client")
        client_uuid = client.get("uuid")
        subscription_url = client.get("subscription_url") or client.get("sub_url")
        print("CREATE_OK")
        print(f"UUID_PRESENT={'yes' if client_uuid else 'no'}")
        print(f"SUBSCRIPTION_URL_PRESENT={'yes' if subscription_url else 'no'}")

    finally:
        if created:
            await _request("DELETE", f"/clients/{quote(name, safe='')}")
            remaining = await get_client(name)
            if remaining is None:
                print("DELETE_OK")
            else:
                print("WARNING: тестовый клиент всё ещё найден в H1")
                raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
