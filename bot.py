import asyncio
import logging
import os
import json
import urllib.error
import urllib.request

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

from database import init_db, get_subscription


H1_PANEL_API_URL = os.getenv("H1_PANEL_API_URL", "").rstrip("/")
H1_PANEL_API_TOKEN = os.getenv("H1_PANEL_API_TOKEN", "")


def check_h1_api_sync():
    """Non-mutating check of H1 API access; never logs the token or response body."""
    if not H1_PANEL_API_URL or not H1_PANEL_API_TOKEN:
        return False, "Не заданы H1_PANEL_API_URL или H1_PANEL_API_TOKEN"

    request = urllib.request.Request(
        f"{H1_PANEL_API_URL}/inbounds",
        headers={
            "Authorization": f"Bearer {H1_PANEL_API_TOKEN}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            body = response.read(1024 * 1024)
        if status < 200 or status >= 300:
            return False, f"H1 API вернул HTTP {status}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, "H1 API ответил, но вернул не JSON"
        if isinstance(payload, dict) and payload.get("ok") is False:
            return False, "H1 API вернул ok=false; проверь токен и адрес (ответ без подробностей для безопасности)"

        # Log only non-sensitive inbound metadata needed for client provisioning.
        records = None
        if isinstance(payload, dict):
            for key in ("inbounds", "items", "data"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    records = candidate
                    break
                if isinstance(candidate, dict):
                    nested = candidate.get("inbounds") or candidate.get("items")
                    if isinstance(nested, list):
                        records = nested
                        break
        if records is not None:
            safe_items = []
            for item in records:
                if not isinstance(item, dict):
                    continue
                inbound_id = item.get("id", item.get("inbound_id", "?"))
                label = item.get("remark") or item.get("name") or item.get("tag") or "без названия"
                protocol = item.get("protocol") or item.get("type") or "не указан"
                safe_items.append(f"id={inbound_id}, name={label}, protocol={protocol}")
            if safe_items:
                return True, "H1 API доступен; инбаунды: " + " | ".join(safe_items[:20])
            return True, f"H1 API доступен (HTTP {status}, JSON получен), но формат списка инбаундов не распознан"

        return True, f"H1 API доступен (HTTP {status}, JSON получен), но поле со списком инбаундов не найдено"
    except urllib.error.HTTPError as exc:
        return False, f"H1 API вернул HTTP {exc.code}"
    except Exception as exc:
        # Avoid printing request headers or credentials; only report exception type.
        return False, f"Не удалось связаться с H1 API ({type(exc).__name__})"


async def check_h1_api():
    return await asyncio.to_thread(check_h1_api_sync)


BOT_TOKEN = os.getenv("BOT_TOKEN")

SUPPORT_URL = "https://t.me/vozduh_vpn_support"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-10-17-72"
OFFER_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-10-17-42"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="☁️ Купить VPN"),
        ],
        [
            KeyboardButton(text="👤 Моя подписка"),
        ],
        [
            KeyboardButton(text="🆘 Помощь"),
        ],
    ],
    resize_keyboard=True,
)


tariffs_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🌱 3 устройства — 299 ₽",
                callback_data="tariff_3",
            )
        ],
        [
            InlineKeyboardButton(
                text="🚀 5 устройств — 399 ₽",
                callback_data="tariff_5",
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡ 7 устройств — 499 ₽",
                callback_data="tariff_7",
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


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "☁️ Добро пожаловать в «Воздух VPN»!\n\n"
        "Надёжное подключение — без лишних сложностей.\n\n"
        "Выбери нужный раздел в меню ниже.",
        reply_markup=main_keyboard,
    )


@dp.message(F.text == "☁️ Купить VPN")
async def buy_vpn(message: Message):
    await message.answer(
        "☁️ Тарифы «Воздух VPN»\n\n"
        "Выбери подходящий вариант:\n\n"
        "🌱 Старт — 3 устройства — 299 ₽\n"
        "🚀 Оптимум — 5 устройств — 399 ₽\n"
        "⚡ Максимум — 7 устройств — 499 ₽\n\n"
        "Цены и подключение появятся позже. "
        "Сейчас покупка ещё недоступна.",
        reply_markup=tariffs_keyboard,
    )


@dp.callback_query(F.data.in_({"tariff_3", "tariff_5", "tariff_7"}))
async def tariff_coming_soon(callback: CallbackQuery):
    tariffs = {
        "tariff_3": (3, 299),
        "tariff_5": (5, 399),
        "tariff_7": (7, 499),
    }
    devices, price = tariffs[callback.data]

    await callback.answer(
        f"Тариф на {devices} устройства — {price} ₽. Скоро появится!",
        show_alert=True,
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.answer(
        "Ты вернулся в главное меню ☁️",
        reply_markup=main_keyboard,
    )
    await callback.answer()


@dp.message(F.text == "👤 Моя подписка")
async def my_subscription(message: Message):
    try:
        subscription = await get_subscription(message.from_user.id)
    except Exception:
        logger.exception("Не удалось прочитать подписку")
        await message.answer(
            "Не удалось проверить подписку. Попробуй немного позже.",
            reply_markup=main_keyboard,
        )
        return

    if not subscription or subscription.get("status") != "active":
        await message.answer(
            "👤 Моя подписка\n\n"
            "Активная подписка пока не найдена.\n"
            "Когда подключение станет доступно, здесь появятся "
            "твой тариф и срок действия.",
            reply_markup=main_keyboard,
        )
        return

    await message.answer(
        "👤 Моя подписка\n\n"
        f"Статус: {subscription.get('status', 'неизвестен')}\n"
        f"Тариф: {subscription.get('tariff') or 'не указан'}\n"
        f"Устройства: {subscription.get('devices') or 'не указано'}\n"
        f"Действует до: {subscription.get('expires_at') or 'не указано'}",
        reply_markup=main_keyboard,
    )


@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(
        "🆘 Помощь по «Воздух VPN»\n\n"
        "Если возник вопрос, напиши в службу поддержки.\n\n"
        "Полезные документы доступны по кнопкам ниже.",
        reply_markup=help_keyboard,
    )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Добавь токен в переменные окружения Bothost."
        )

    await init_db()

    if H1_PANEL_API_URL and H1_PANEL_API_TOKEN:
        h1_ok, h1_message = await check_h1_api()
        if h1_ok:
            logger.info(h1_message)
        else:
            logger.warning(h1_message)
    else:
        logger.warning(
            "H1 API пока не проверен: добавь H1_PANEL_API_URL и "
            "H1_PANEL_API_TOKEN в переменные окружения Bothost."
        )

    bot = Bot(token=BOT_TOKEN)

    try:
        logger.info("Бот запускается")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
