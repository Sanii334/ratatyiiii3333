import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, LabeledPrice, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

from database import (
    init_db, get_subscription, save_subscription,
    mark_trial_used, is_trial_available
)
from xui_client import create_vpn_client

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не задан токен бота")

BOT_NAME = os.getenv("BOT_NAME", "VPN")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))
TRIAL_DEVICES = int(os.getenv("TRIAL_DEVICES", "1"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))

TARIFFS = {
    "1d": {
        "title": os.getenv("TARIFF_1D_TITLE", "VPN на 1 устройство"),
        "stars": int(os.getenv("TARIFF_1D_STARS", "100")),
        "devices": int(os.getenv("TARIFF_1D_DEVICES", "1")),
    },
    "3d": {
        "title": os.getenv("TARIFF_3D_TITLE", "VPN на 3 устройства"),
        "stars": int(os.getenv("TARIFF_3D_STARS", "200")),
        "devices": int(os.getenv("TARIFF_3D_DEVICES", "3")),
    },
    "5d": {
        "title": os.getenv("TARIFF_5D_TITLE", "VPN на 5 устройств"),
        "stars": int(os.getenv("TARIFF_5D_STARS", "300")),
        "devices": int(os.getenv("TARIFF_5D_DEVICES", "5")),
    },
    "7d": {
        "title": os.getenv("TARIFF_7D_TITLE", "VPN на 7 устройств"),
        "stars": int(os.getenv("TARIFF_7D_STARS", "400")),
        "devices": int(os.getenv("TARIFF_7D_DEVICES", "7")),
    },
}


@dp.message(CommandStart())
async def start(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить VPN", callback_data="buy_menu")],
        [InlineKeyboardButton(text="🎁 Пробный период", callback_data="trial")],
        [InlineKeyboardButton(text="📋 Моя подписка", callback_data="my_sub")],
    ])
    await message.answer(
        f"Привет! Это {BOT_NAME} ☁️\n\n"
        "Здесь вы можете купить доступ к быстрым серверам.",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "trial")
async def trial(callback: CallbackQuery):
    user_id = callback.from_user.id

    if not await is_trial_available(user_id):
        await callback.answer("Вы уже использовали пробный период.", show_alert=True)
        return

    await callback.message.answer("⏳ Создаём ваш пробный доступ, подождите...")

    try:
        email, link = create_vpn_client(
            user_id=user_id,
            days=TRIAL_DAYS,
            limit_ip=TRIAL_DEVICES,
            is_trial=True
        )

        if not link:
            await callback.message.answer("❌ Не удалось получить ссылку. Напишите в поддержку.")
            await callback.answer()
            return

        await mark_trial_used(user_id)

        text = (
            f"🎁 Пробный доступ активирован!\n\n"
            f"Срок: {TRIAL_DAYS} дней с момента первого подключения\n"
            f"Устройств: {TRIAL_DEVICES}\n\n"
            f"Ваш ключ:\n`{link}`\n\n"
            f"Скопируйте его и вставьте в приложение (Happ, INCY, v2RayTun).\n"
            f"Важно: отсчёт начнётся после первого подключения."
        )

        if SUPPORT_CONTACT:
            text += f"\n\nПоддержка: {SUPPORT_CONTACT}"

        await callback.message.answer(text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка создания пробного клиента: {e}")
        await callback.message.answer("❌ Произошла ошибка. Напишите в поддержку.")

    await callback.answer()


@dp.callback_query(F.data == "buy_menu")
async def buy_menu(callback: CallbackQuery):
    buttons = []
    for key, tariff in TARIFFS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{tariff['title']} — {tariff['stars']} ⭐",
            callback_data=f"pay_{key}"
        )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Выберите тариф:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("pay_"))
async def send_invoice(callback: CallbackQuery):
    tariff_key = callback.data.split("_")[1]
    tariff = TARIFFS.get(tariff_key)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=tariff["title"],
        description=f"Доступ к VPN на {SUBSCRIPTION_DAYS} дней, {tariff['devices']} устройств",
        payload=f"vpn_{tariff_key}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=tariff["title"], amount=tariff["stars"])],
    )
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def on_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    tariff_key = payload.replace("vpn_", "")
    tariff = TARIFFS.get(tariff_key)

    if not tariff:
        await message.answer("Ошибка: тариф не найден.")
        return

    user_id = message.from_user.id
    username = message.from_user.username

    await message.answer("⏳ Создаём ваш VPN-доступ, подождите...")

    try:
        email, link = create_vpn_client(
            user_id=user_id,
            days=SUBSCRIPTION_DAYS,
            limit_ip=tariff["devices"]
        )

        if not link:
            await message.answer("❌ Не удалось получить ссылку. Напишите в поддержку.")
            return

        await save_subscription(
            user_id=user_id,
            username=username,
            tariff=tariff["title"],
            devices=tariff["devices"],
            days=SUBSCRIPTION_DAYS
        )

        text = (
            f"✅ Оплата получена!\n\n"
            f"Тариф: {tariff['title']}\n"
            f"Устройств: {tariff['devices']}\n"
            f"Срок: {SUBSCRIPTION_DAYS} дней\n\n"
            f"Ваш ключ для подключения:\n`{link}`\n\n"
            f"Скопируйте его и вставьте в приложение (Happ, INCY, v2RayTun)."
        )

        if SUPPORT_CONTACT:
            text += f"\n\nПоддержка: {SUPPORT_CONTACT}"

        await message.answer(text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка создания клиента: {e}")
        await message.answer("❌ Произошла ошибка при создании доступа. Напишите в поддержку.")


@dp.callback_query(F.data == "my_sub")
async def my_sub(callback: CallbackQuery):
    sub = await get_subscription(callback.from_user.id)
    if not sub or sub["status"] != "active":
        await callback.message.answer("У вас нет активной подписки.")
    else:
        await callback.message.answer(
            f"Тариф: {sub['tariff']}\n"
            f"Устройств: {sub['devices']}\n"
            f"Действует до: {sub['expires_at'][:10]}"
        )
    await callback.answer()


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
