import asyncio
import logging
import os

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
    claim_payment_issuance,
    finish_payment_issuance,
    fail_payment_issuance,
)
from h1_client import H1APIError, H1_INBOUND_ID, create_vpn_client, get_inbounds

BOT_TOKEN = os.getenv("BOT_TOKEN")

SUPPORT_URL = "https://t.me/vozduh_vpn_support"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-10-17-72"
OFFER_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-10-17-42"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        [InlineKeyboardButton(text="🌱 3 устройства — 299 ₽", callback_data="tariff_3")],
        [InlineKeyboardButton(text="🚀 5 устройств — 399 ₽", callback_data="tariff_5")],
        [InlineKeyboardButton(text="⚡ 7 устройств — 499 ₽", callback_data="tariff_7")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_to_menu")],
    ]
)

help_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать в поддержку", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=PRIVACY_URL)],
        [InlineKeyboardButton(text="📄 Публичная оферта", url=OFFER_URL)],
    ]
)

dp = Dispatcher()


async def issue_subscription_after_verified_payment(
    *,
    payment_id: str,
    user_id: int,
    username: str | None,
    tariff: str,
    devices: int,
    days: int,
):
    """Вызывайте только после серверной проверки успешного платежа у провайдера.

    Функция предназначена для первой выдачи подписки. Продление существующих
    клиентов требует отдельного сценария и намеренно здесь не выполняется.
    """
    if not payment_id or not payment_id.strip():
        raise ValueError("Не указан ID подтверждённого платежа")
    if devices not in (3, 5, 7):
        raise ValueError("Допустимое число устройств: 3, 5 или 7")
    if days <= 0:
        raise ValueError("Срок подписки должен быть больше нуля")

    claimed = await claim_payment_issuance(payment_id.strip(), user_id)
    if not claimed:
        return {"issued": False, "reason": "duplicate_payment"}

    try:
        client = await create_vpn_client(
            user_id=user_id,
            days=days,
            device_limit=devices,
        )
        subscription_key = client.get("subscription_url") or client.get("sub_url")
        client_id = client.get("uuid") or client.get("id") or f"tg_{user_id}"

        if not isinstance(subscription_key, str) or not subscription_key.startswith("https://"):
            raise H1APIError("H1 не вернул корректную HTTPS-ссылку подписки")

        await save_subscription(
            user_id=user_id,
            username=username,
            tariff=tariff,
            devices=devices,
            days=days,
            vpn_client_id=str(client_id),
            vpn_key=subscription_key,
        )
        await finish_payment_issuance(payment_id.strip())
        return {
            "issued": True,
            "subscription_url": subscription_key,
            "expires_at": client.get("expires_at"),
        }
    except Exception as exc:
        await fail_payment_issuance(payment_id.strip(), type(exc).__name__)
        raise


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
        "Цены и подключение появятся позже. Сейчас покупка ещё недоступна.",
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
            "Когда подключение станет доступно, здесь появятся твой тариф и срок действия.",
            reply_markup=main_keyboard,
        )
        return

    details = [
        "👤 Моя подписка",
        "",
        f"Статус: {subscription.get('status', 'неизвестен')}",
        f"Тариф: {subscription.get('tariff') or 'не указан'}",
        f"Устройства: {subscription.get('devices') or 'не указано'}",
        f"Действует до: {subscription.get('expires_at') or 'не указано'}",
    ]
    vpn_key = subscription.get("vpn_key")
    if vpn_key:
        details.extend(["", "🔑 Ссылка на подключение:", vpn_key])

    await message.answer("\n".join(details), reply_markup=main_keyboard)


@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(
        "🆘 Помощь по «Воздух VPN»\n\n"
        "Если возник вопрос, напиши в службу поддержки.\n\n"
        "Полезные документы доступны по кнопкам ниже.",
        reply_markup=help_keyboard,
    )


async def check_h1_on_startup():
    try:
        inbounds = await get_inbounds()
        if not inbounds:
            logger.warning("H1 API доступен, но список инбаундов пустой")
            return

        matching_inbound = any(
            str(item.get("id", item.get("inbound_id", ""))) == str(H1_INBOUND_ID)
            for item in inbounds
        )
        logger.info("H1 API доступен; инбаундов получено: %s", len(inbounds))
        if not matching_inbound:
            logger.warning("H1_INBOUND_ID не найден среди инбаундов панели; выдача подписок не сработает")
    except H1APIError as exc:
        logger.warning("Проверка H1 API не прошла: %s", str(exc))


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Добавь токен в переменные окружения Bothost.")

    await init_db()
    await check_h1_on_startup()

    bot = Bot(token=BOT_TOKEN)
    try:
        logger.info("Бот запускается")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
