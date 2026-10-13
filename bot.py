
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

from database import init_db, get_subscription


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

SUPPORT_URL = "https://t.me/vozduh_vpn_support"
PRIVACY_URL = "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99"
OFFER_URL = "https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# ГЛАВНОЕ МЕНЮ
# =========================

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


# =========================
# МЕНЮ ТАРИФОВ
# =========================

tariffs_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🌱 3 устройства — скоро",
                callback_data="tariff_3",
            )
        ],
        [
            InlineKeyboardButton(
                text="🚀 5 устройств — скоро",
                callback_data="tariff_5",
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡ 7 устройств — скоро",
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


# =========================
# МЕНЮ ПОМОЩИ
# =========================

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


# =========================
# ЗАПУСК БОТА
# =========================

dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "☁️ Добро пожаловать в «Воздух VPN»!\n\n"
        "Надёжное подключение — без лишних сложностей.\n\n"
        "Выбери нужный раздел в меню ниже.",
        reply_markup=main_keyboard,
    )


# =========================
# ПОКУПКА VPN
# =========================

@dp.message(F.text == "☁️ Купить VPN")
async def buy_vpn(message: Message):
    await message.answer(
        "☁️ Тарифы «Воздух VPN»\n\n"
        "Выбери подходящий вариант:\n\n"
        "🌱 Старт — 3 устройства\n"
        "🚀 Оптимум — 5 устройств\n"
        "⚡ Максимум — 7 устройств\n\n"
        "Цены и подключение появятся позже. "
        "Сейчас покупка ещё недоступна.",
        reply_markup=tariffs_keyboard,
    )


@dp.callback_query(F.data.in_({"tariff_3", "tariff_5", "tariff_7"}))
async def tariff_coming_soon(callback: CallbackQuery):
    devices = {
        "tariff_3": 3,
        "tariff_5": 5,
        "tariff_7": 7,
    }[callback.data]

    await callback.answer(
        f"Тариф на {devices} устройства — скоро появится!",
        show_alert=True,
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.answer(
        "Ты вернулся в главное меню ☁️",
        reply_markup=main_keyboard,
    )
    await callback.answer()


# =========================
# МОЯ ПОДПИСКА
# =========================

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


# =========================
# ПОМОЩЬ
# =========================

@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(
        "🆘 Помощь по «Воздух VPN»\n\n"
        "Если возник вопрос, напиши в службу поддержки.\n\n"
        "Полезные документы доступны по кнопкам ниже.",
        reply_markup=help_keyboard,
    )


# =========================
# MAIN
# =========================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Добавь токен в переменные окружения Bothost."
        )

    await init_db()

    bot = Bot(token=BOT_TOKEN)

    try:
        logger.info("Бот запускается")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```
