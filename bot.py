import asyncio
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from database import (
    init_db,
    get_subscription,
    save_subscription,
    save_payment,
    get_payment,
    get_pending_payments,
    update_payment_status,
    claim_payment_issuance,
    finish_payment_issuance,
    fail_payment_issuance,
    claim_trial,
)

from h1_client import (
    H1APIError,
    H1_INBOUND_ID,
    create_vpn_client,
    get_client,
    get_inbounds,
    update_client_device_limit,
)

from platega_client import (
    MERCHANT_ID,
    PlategaAPIError,
    create_payment,
    get_transaction,
    is_configured as platega_is_configured,
)


BOT_TOKEN = os.getenv("BOT_TOKEN")
SUBSCRIPTION_DAYS = max(1, int(os.getenv("SUBSCRIPTION_DAYS", "30")))
PAYMENT_CHECK_INTERVAL = max(
    10, int(os.getenv("PAYMENT_CHECK_INTERVAL", "20"))
)

# Ссылки сохраняем без изменений
SUPPORT_URL = "https://t.me/vozduh_vpn_support"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-10-17-72"
OFFER_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-10-17-42"


TARIFFS = {
    "tariff_3": {
        "devices": 3,
        "amount": 81,
        "name": "Старт",
    },
    "tariff_5": {
        "devices": 5,
        "amount": 135,
        "name": "Комфорт",
    },
    "tariff_7": {
        "devices": 7,
        "amount": 189,
        "name": "Максимум",
    },
    "tariff_10": {
        "devices": 10,
        "amount": 243,
        "name": "Семейный",
    },
}

PRICE_BY_DEVICES = {
    plan["devices"]: plan["amount"]
    for plan in TARIFFS.values()
}


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

dp = Dispatcher()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="☁️ Купить VPN")],
        [KeyboardButton(text="👤 Моя подписка")],
        [KeyboardButton(text="🆘 Помощь")],
    ],
    resize_keyboard=True,
)


tariffs_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🌬️ Старт — 3 устройства · 81 ₽/мес",
                callback_data="tariff_3",
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡ Комфорт — 5 устройств · 135 ₽/мес",
                callback_data="tariff_5",
            )
        ],
        [
            InlineKeyboardButton(
                text="🚀 Максимум — 7 устройств · 189 ₽/мес",
                callback_data="tariff_7",
            )
        ],
        [
            InlineKeyboardButton(
                text="👨‍👩‍👧‍👦 Семейный — 10 устройств · 243 ₽/мес",
                callback_data="tariff_10",
            )
        ],
        [
            InlineKeyboardButton(
                text="🎁 Бесплатные 7 дней",
                callback_data="trial_start",
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="back_to_menu",
            )
        ],
    ]
)


help_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💬 Написать в поддержку",
                url=SUPPORT_URL,
            )
        ],
        [
            InlineKeyboardButton(
                text="🔒 Политика конфиденциальности",
                url=PRIVACY_URL,
            )
        ],
        [
            InlineKeyboardButton(
                text="📄 Публичная оферта",
                url=OFFER_URL,
            )
        ],
    ]
)


def payment_keyboard(payment_url: str, transaction_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Перейти к оплате",
                    url=payment_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Проверить оплату",
                    callback_data=f"paycheck:{transaction_id}",
                )
            ],
        ]
    )


def parse_expiry(value):
    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def tariff_for_devices(devices):
    return next(
        (
            plan["name"]
            for plan in TARIFFS.values()
            if plan["devices"] == int(devices)
        ),
        "Неизвестный",
    )


def prorated_upgrade_price(
    current_devices,
    target_devices,
    expires_at,
):
    """Стоимость апгрейда за оставшийся срок подписки."""
    expiry = parse_expiry(expires_at)

    if not expiry or target_devices <= current_devices:
        return 0

    seconds_left = max(
        0,
        (expiry - datetime.now(timezone.utc)).total_seconds(),
    )

    if seconds_left <= 0:
        return 0

    difference = (
        PRICE_BY_DEVICES[target_devices]
        - PRICE_BY_DEVICES[current_devices]
    )

    amount = (
        Decimal(difference)
        * Decimal(str(seconds_left))
        / Decimal(30 * 86400)
    )

    return int(
        amount.to_integral_value(rounding=ROUND_CEILING)
    )


async def issue_subscription_after_verified_payment(
    *,
    payment_id,
    user_id,
    username,
    tariff,
    devices,
    days,
):
    if devices not in (3, 5, 7, 10) or days <= 0:
        raise ValueError("Некорректные параметры подписки")

    if not await claim_payment_issuance(payment_id, user_id):
        return {
            "issued": False,
            "reason": "duplicate_payment",
        }

    try:
        try:
            client = await create_vpn_client(
                user_id=user_id,
                days=days,
                device_limit=devices,
            )
        except H1APIError as exc:
            if "уже существует" not in str(exc):
                raise

            client = await get_client(f"tg_{user_id}")

            if (
                not client
                or int(client.get("device_limit", -1)) != devices
            ):
                raise

        key = (
            client.get("subscription_url")
            or client.get("sub_url")
        )

        client_id = (
            client.get("uuid")
            or client.get("id")
            or f"tg_{user_id}"
        )

        if not isinstance(key, str) or not key.startswith("https://"):
            raise H1APIError(
                "H1 не вернула корректную HTTPS-ссылку подписки"
            )

        expiry = client.get("expires_at")

        await save_subscription(
            user_id=user_id,
            username=username,
            tariff=tariff,
            devices=devices,
            days=days,
            vpn_client_id=str(client_id),
            vpn_key=key,
            expires_at=expiry,
        )

        await finish_payment_issuance(payment_id)

        return {
            "issued": True,
            "subscription_url": key,
            "expires_at": expiry,
        }

    except Exception as exc:
        await fail_payment_issuance(
            payment_id,
            type(exc).__name__,
        )
        raise
