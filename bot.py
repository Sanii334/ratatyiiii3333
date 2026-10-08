
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("Не задан токен бота")

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Это Воздух VPN ☁️\n\n"
        "Бот находится в разработке."
    )


async def main():
    logging.basicConfig(level=logging.INFO)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())