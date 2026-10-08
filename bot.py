
import asyncio
import logging
import os
from database import init_db

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# Настройки
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("Не задан токен бота. Проверь переменную BOT_TOKEN.")

SUPPORT_URL = "https://t.me/vozduh_vpn_support"

PRIVACY_URL = (
    "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99"
)
OFFER_URL = "https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15"

bot = Bot(token=TOKEN)
dp = Dispatcher()


# Главное меню
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="☁️ Купить VPN")],
        [KeyboardButton(text="👤 Моя подписка")],
        [KeyboardButton(text="🆘 Помощь")],
    ],
    resize_keyboard=True,
)


# Кнопки с документами
documents_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔒 Политика конфиденциальности",
                url=PRIVACY_URL,
            )
        ],
        [
            InlineKeyboardButton(
                text="📄 Пользовательское соглашение",
                url=OFFER_URL,
            )
        ],
    ]
)


# /start
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "☁️ Добро пожаловать в «Воздух VPN»!\n\n"
        "Мы помогаем организовать защищённое подключение "
        "к интернету.\n\n"
        "Выбери нужный раздел в меню ниже.",
        reply_markup=main_keyboard,
    )


# Покупка VPN
@dp.message(F.text == "☁️ Купить VPN")
async def buy_vpn(message: Message):
    await message.answer(
        "☁️ Покупка «Воздух VPN»\n\n"
        "Мы готовим тарифы и подключение оплаты.\n\n"
        "Покупка и автоматическая выдача VPN-ключа "
        "пока не подключены.\n\n"
        "Пожалуйста, не переводите деньги по сторонним "
        "реквизитам, которые не подтверждены ботом."
    )


# Моя подписка
@dp.message(F.text == "👤 Моя подписка")
async def my_subscription(message: Message):
    await message.answer(
        "👤 Моя подписка\n\n"
        "Активная подписка пока не найдена.\n\n"
        "Когда система подписок будет подключена, "
        "здесь появятся:\n"
        "• Статус подписки\n"
        "• Название тарифа\n"
        "• Количество устройств\n"
        "• Дата окончания\n"
        "• Данные для подключения VPN\n\n"
        "Выдача ключей пока не подключена."
    )


# Помощь
@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer(
        "🆘 Поддержка «Воздух VPN»\n\n"
        "Если у тебя возник вопрос по оплате, подписке "
        "или подключению, напиши нашей поддержке:\n"
        "@vozduh_vpn_support\n\n"
        "Документы сервиса доступны по кнопкам ниже.",
        reply_markup=documents_keyboard,
    )


# Обработка неизвестных сообщений
@dp.message()
async def unknown_message(message: Message):
    await message.answer(
        "Я пока не понимаю эту команду.\n\n"
        "Пожалуйста, выбери один из разделов главного меню.",
        reply_markup=main_keyboard,
    )


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
