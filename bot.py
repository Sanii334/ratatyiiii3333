import asyncio
import logging
import os
import math
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    init_db, get_subscription, save_subscription, upgrade_subscription_devices,
    save_payment, get_payment, get_pending_payments, update_payment_status,
    claim_payment_issuance, finish_payment_issuance, fail_payment_issuance,
    claim_trial, cancel_trial_claim
)
from h1_client import (
    H1APIError, H1_INBOUND_ID, create_vpn_client, get_client,
    get_inbounds, update_client_device_limit
)
from platega_client import MERCHANT_ID, PlategaAPIError, create_payment, get_transaction, is_configured as platega_is_configured

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUBSCRIPTION_DAYS = max(1, int(os.getenv("SUBSCRIPTION_DAYS", "30")))
PAYMENT_CHECK_INTERVAL = max(10, int(os.getenv("PAYMENT_CHECK_INTERVAL", "20")))
SUPPORT_URL = "https://t.me/vozduh_vpn_support"
PRIVACY_URL = "https://telegra.ph/Politika-konfidencialnosti-10-17-72"
OFFER_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-10-17-42"

# Единый источник правды по тарифам
TARIFFS = {
    "tariff_3":  {"devices": 3,  "amount": 81,  "name": "Старт"},
    "tariff_5":  {"devices": 5,  "amount": 135, "name": "Комфорт"},
    "tariff_7":  {"devices": 7,  "amount": 189, "name": "Максимум"},
    "tariff_10": {"devices": 10, "amount": 243, "name": "Семейный"},
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
dp = Dispatcher()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="☁️ Купить VPN")],
        [KeyboardButton(text="🎁 Пробный период")],
        [KeyboardButton(text="📱 Моя подписка")],
        [KeyboardButton(text="🆘 Помощь")]
    ],
    resize_keyboard=True,
)

help_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать в поддержку", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=PRIVACY_URL)],
        [InlineKeyboardButton(text="📄 Публичная оферта", url=OFFER_URL)],
    ]
)

def payment_keyboard(payment_url: str, transaction_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"paycheck:{transaction_id}")],
    ])

def get_tariffs_keyboard():
    buttons = []
    for key, plan in TARIFFS.items():
        buttons.append([InlineKeyboardButton(text=f"{plan['name']} — {plan['devices']} устр. — {plan['amount']} ₽", callback_data=key)])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_tariff_by_devices(devices: int) -> dict | None:
    for plan in TARIFFS.values():
        if plan["devices"] == devices:
            return plan
    return None

def get_tariff_by_name(name: str) -> dict | None:
    for plan in TARIFFS.values():
        if plan["name"] == name:
            return plan
    return None

# --- ПЛАТЕЖИ И ВЫДАЧА ---
async def issue_subscription_after_verified_payment(*, payment_id: str, user_id: int, username: str | None, tariff: str, devices: int, days: int):
    if not payment_id or not payment_id.strip():
        raise ValueError("Не указан ID подтверждённого платежа")
    if devices not in (3, 5, 7, 10) or days <= 0:
        raise ValueError("Некорректные параметры подписки")
    
    if not await claim_payment_issuance(payment_id.strip(), user_id):
        return {"issued": False, "reason": "duplicate_payment"}
    
    try:
        try:
            client = await create_vpn_client(user_id=user_id, days=days, device_limit=devices)
        except H1APIError as exc:
            if "уже существует" not in str(exc):
                raise
            client = await get_client(f"tg_{user_id}")
            existing_devices = client.get("device_limit") if client else None
            if client is None or existing_devices is None or int(existing_devices) != devices:
                raise
        
        subscription_key = client.get("subscription_url") or client.get("sub_url")
        client_id = client.get("uuid") or client.get("id") or f"tg_{user_id}"
        if not isinstance(subscription_key, str) or not subscription_key.startswith("https://"):
            raise H1APIError("H1 не вернул корректную HTTPS-ссылку подписки")
        
        expires_at = await save_subscription(user_id=user_id, username=username, tariff=tariff, devices=devices, days=days, vpn_client_id=str(client_id), vpn_key=subscription_key)
        await finish_payment_issuance(payment_id.strip())
        return {"issued": True, "subscription_url": subscription_key, "expires_at": client.get("expires_at") or expires_at}
    except Exception as exc:
        await fail_payment_issuance(payment_id.strip(), type(exc).__name__)
        raise

async def process_upgrade_after_verified_payment(*, payment_id: str, user_id: int, target_devices: int, new_tariff: str):
    if not await claim_payment_issuance(payment_id, user_id):
        return {"issued": False, "reason": "duplicate_payment"}
    
    try:
        sub = await get_subscription(user_id)
        if not sub or sub.get("status") != "active" or not sub.get("vpn_client_id"):
            raise H1APIError("Нет активной подписки для апгрейда")
        
        client_name = f"tg_{user_id}"
        await update_client_device_limit(client_name, target_devices)
        await upgrade_subscription_devices(user_id, new_tariff=new_tariff, new_devices=target_devices)
        await finish_payment_issuance(payment_id)
        return {"issued": True, "new_tariff": new_tariff, "devices": target_devices}
    except Exception as exc:
        await fail_payment_issuance(payment_id, type(exc).__name__)
        raise

async def process_payment(transaction_id: str, expected_user_id: int):
    payment = await get_payment(transaction_id)
    if not payment or int(payment["user_id"]) != expected_user_id:
        return {"status": "not_found", "issued_now": False}
    if payment["status"] == "issued":
        return {"status": "issued", "issued_now": False}

    transaction = await get_transaction(transaction_id)
    returned_id = transaction.get("id") or transaction.get("transactionId")
    if str(returned_id or "") != transaction_id:
        raise PlategaAPIError("ID транзакции в ответе Platega не совпал")
    response_merchant = transaction.get("merchantId") or transaction.get("mechantId")
    if response_merchant and str(response_merchant) != MERCHANT_ID:
        raise PlategaAPIError("Транзакция принадлежит другому магазину")
    
    status = str(transaction.get("status", "")).upper()
    if status == "CONFIRMED":
        details = transaction.get("paymentDetails")
        if not isinstance(details, dict):
            raise PlategaAPIError("В ответе Platega отсутствуют данные суммы платежа")
        try:
            actual_amount = Decimal(str(details.get("amount")))
            expected_amount = Decimal(str(payment["amount"]))
        except (InvalidOperation, TypeError):
            raise PlategaAPIError("Platega вернул некорректную сумму") from None
        if actual_amount != expected_amount or str(details.get("currency", "")).upper() != "RUB":
            raise PlategaAPIError("Сумма или валюта платежа не совпадает с заказом")
        
        await update_payment_status(transaction_id, "confirmed")
        kind = payment.get("kind", "subscription")
        
        if kind == "upgrade":
            result = await process_upgrade_after_verified_payment(
                payment_id=transaction_id,
                user_id=int(payment["user_id"]),
                target_devices=int(payment["target_devices"]),
                new_tariff=payment["tariff"]
            )
        else:
            result = await issue_subscription_after_verified_payment(
                payment_id=transaction_id,
                user_id=int(payment["user_id"]),
                username=payment.get("username"),
                tariff=payment["tariff"],
                devices=int(payment["devices"]),
                days=int(payment["days"]),
            )
            
        if result.get("issued"):
            await update_payment_status(transaction_id, "issued")
            return {"status": "issued", "issued_now": True, "kind": kind, **result}
        latest = await get_payment(transaction_id)
        return {"status": "issued" if latest and latest.get("status") == "issued" else "processing", "issued_now": False}
    
    if status in {"CANCELED", "EXPIRED", "FAILED"}:
        await update_payment_status(transaction_id, status.lower())
        return {"status": status.lower(), "issued_now": False}
    if status == "CHARGEBACKED":
        await update_payment_status(transaction_id, "chargebacked")
        return {"status": "chargebacked", "issued_now": False}
    return {"status": "pending", "issued_now": False}

async def notify_issued(bot: Bot, user_id: int, result: dict):
    if result.get("kind") == "upgrade":
        text = (f"✅ Апгрейд подтверждён! «Воздух VPN» обновлён.\n\n"
                f"Твой новый тариф: {result['new_tariff']}\n"
                f"Количество устройств: {result['devices']}\n\n"
                f"Срок действия подписки и ключ остались прежними. Ссылка доступна в разделе «📱 Моя подписка».")
    else:
        text = ("✅ Оплата подтверждена! «Воздух VPN» готов к работе.\n\n"
                f"Ссылка на подключение:\n{result['subscription_url']}\n\n"
                "Сохрани ссылку и не передавай её другим людям. Она доступна также в разделе «📱 Моя подписка».")
    await bot.send_message(user_id, text, reply_markup=main_keyboard)

# --- ХЭНДЛЕРЫ КОМАНД ---
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("☁️ Добро пожаловать в «Воздух VPN»!\n\nНадёжное подключение — без лишних сложностей.\n\nВыбери нужный раздел в меню ниже.", reply_markup=main_keyboard)

@dp.message(F.text == "☁️ Купить VPN")
async def buy_vpn(message: Message):
    lines = ["☁️ Тарифы «Воздух VPN»\n"]
    for plan in TARIFFS.values():
        lines.append(f"{plan['name']} — {plan['devices']} устр. — {plan['amount']} ₽ за {SUBSCRIPTION_DAYS} дней")
    lines.append("\nВыбери тариф для создания платёжной ссылки.")
    await message.answer("\n".join(lines), reply_markup=get_tariffs_keyboard())

@dp.callback_query(F.data.in_(set(TARIFFS.keys())))
async def choose_tariff(callback: CallbackQuery):
    if not platega_is_configured():
        await callback.answer("Оплата ещё не настроена. Напиши в поддержку.", show_alert=True)
        return
    try:
        subscription = await get_subscription(callback.from_user.id)
    except Exception:
        logger.exception("Не удалось проверить существующую подписку")
        await callback.answer("Не удалось проверить подписку. Попробуй позже.", show_alert=True)
        return
    if subscription and (subscription.get("vpn_client_id") or subscription.get("status") == "active"):
        await callback.answer("У тебя уже есть активная подписка. Используй раздел «📱 Моя подписка» для апгрейда.", show_alert=True)
        return
    
    plan = TARIFFS[callback.data]
    try:
        info = await create_payment(user_id=callback.from_user.id, username=callback.from_user.username, amount=plan["amount"], devices=plan["devices"], days=SUBSCRIPTION_DAYS)
        await save_payment(transaction_id=info["transaction_id"], user_id=callback.from_user.id, username=callback.from_user.username, tariff=plan["name"], devices=plan["devices"], amount=plan["amount"], days=SUBSCRIPTION_DAYS, kind="subscription")
    except Exception as exc:
        logger.warning("Не удалось создать платёж: %s", type(exc).__name__)
        await callback.message.answer("Не удалось создать платёж. Попробуй позже или напиши в поддержку.")
        await callback.answer()
        return
    
    await callback.message.answer(f"🧾 Заказ создан\n\nТариф: {plan['name']}\nУстройства: {plan['devices']}\nСрок: {SUBSCRIPTION_DAYS} дней\nСумма: {plan['amount']} ₽\n\nОплати заказ по кнопке ниже. Бот проверит статус оплаты и выдаст ссылку после подтверждения Platega.", reply_markup=payment_keyboard(info["payment_url"], info["transaction_id"]))
    await callback.answer()

# --- ТРИАЛ ---
@dp.message(F.text == "🎁 Пробный период")
async def trial_period(message: Message):
    user_id = message.from_user.id
    try:
        sub = await get_subscription(user_id)
        if sub and sub.get("status") == "active" and sub.get("vpn_client_id"):
            await message.answer("У тебя уже есть активная подписка. Пробный период доступен только один раз и только при отсутствии платной подписки.", reply_markup=main_keyboard)
            return
        
        # Атомарно занимаем триал
        if not await claim_trial(user_id):
            await message.answer("Ты уже использовал бесплатный пробный период. Больше он не доступен.", reply_markup=main_keyboard)
            return
        
        await message.answer("⏳ Активируем твой бесплатный пробный период на 3 дня...")
        
        try:
            client = await create_vpn_client(user_id=user_id, days=3, device_limit=3)
        except Exception:
            await cancel_trial_claim(user_id) # Откатываем, если H1 упал
            raise
        
        subscription_key = client.get("subscription_url") or client.get("sub_url")
        client_id = client.get("uuid") or client.get("id") or f"tg_{user_id}"
        
        if not isinstance(subscription_key, str) or not subscription_key.startswith("https://"):
            raise H1APIError("H1 не вернул корректную HTTPS-ссылку подписки")
            
        await save_subscription(user_id=user_id, username=message.from_user.username, tariff="Пробный (3 дня)", devices=3, days=3, vpn_client_id=str(client_id), vpn_key=subscription_key)
        
        await message.answer(f"✅ Пробный период активирован на 3 дня!\n\n🔑 Ссылка на подключение:\n{subscription_key}\n\nСохрани ссылку, она также доступна в разделе «📱 Моя подписка».", reply_markup=main_keyboard)
    except Exception as e:
        logger.exception("Ошибка активации триала")
        await message.answer("Не удалось активировать пробный период. Попробуй позже или напиши в поддержку.", reply_markup=main_keyboard)

# --- ПРОВЕРКА ОПЛАТЫ ---
@dp.callback_query(F.data.startswith("paycheck:"))
async def check_payment_callback(callback: CallbackQuery, bot: Bot):
    transaction_id = callback.data.partition(":")[2]
    try:
        result = await process_payment(transaction_id, callback.from_user.id)
    except (PlategaAPIError, H1APIError) as exc:
        logger.warning("Проверка или выдача платежа не прошла: %s", type(exc).__name__)
        await callback.answer("Не удалось завершить проверку. Попробуй позже.", show_alert=True)
        return
    except Exception:
        logger.exception("Ошибка обработки платежа")
        await callback.answer("Возникла ошибка. Попробуй позже или напиши в поддержку.", show_alert=True)
        return
    
    if result["issued_now"]:
        await notify_issued(bot, callback.from_user.id, result)
        await callback.answer("Оплата подтверждена!")
    elif result["status"] == "issued":
        await callback.answer("VPN уже выдан. Ссылка находится в разделе «📱 Моя подписка».", show_alert=True)
    elif result["status"] == "pending":
        await callback.answer("Оплата пока не подтверждена. Попробуй ещё раз позже.", show_alert=True)
    elif result["status"] in {"canceled", "expired", "failed"}:
        await callback.answer("Платёж отменён или срок оплаты истёк. Создай новый заказ.", show_alert=True)
    elif result["status"] == "chargebacked":
        await callback.answer("Провайдер сообщил о возврате. Напиши в поддержку.", show_alert=True)
    elif result["status"] == "not_found":
        await callback.answer("Платёж не найден для твоего аккаунта.", show_alert=True)
    else:
        await callback.answer("Платёж подтверждён, выдача VPN обрабатывается. Проверь ещё раз через минуту.", show_alert=True)

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.answer("Ты вернулся в главное меню ☁️", reply_markup=main_keyboard)
    await callback.answer()

# --- МОЯ ПОДПИСКА ---
@dp.message(F.text == "📱 Моя подписка")
async def my_subscription(message: Message):
    try:
        subscription = await get_subscription(message.from_user.id)
    except Exception:
        logger.exception("Не удалось прочитать подписку")
        await message.answer("Не удалось проверить подписку. Попробуй немного позже.", reply_markup=main_keyboard)
        return

    if not subscription or subscription.get("status") != "active":
        await message.answer("📱 Моя подписка\n\nАктивная подписка пока не найдена.", reply_markup=main_keyboard)
        return

    details = [
        "📱 Моя подписка", "",
        f"Статус: {subscription.get('status', 'неизвестен')}",
        f"Тариф: {subscription.get('tariff') or 'не указан'}",
        f"Устройства: {subscription.get('devices') or 'не указано'}",
        f"Действует до: {subscription.get('expires_at') or 'не указано'}"
    ]
    if subscription.get("vpn_key"):
        details.extend(["", "🔑 Ссылка на подключение:", subscription["vpn_key"]])

    # Проверяем возможность апгрейда
    upgrade_kb = None
    current_devices = subscription.get("devices", 0)
    if current_devices < 10 and subscription.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(subscription["expires_at"])
            remaining_days = (expires_at - datetime.now(timezone.utc)).days
            if remaining_days >= 1:
                buttons = []
                current_plan = get_tariff_by_name(subscription.get("tariff"))
                if current_plan:
                    for plan in TARIFFS.values():
                        if plan["devices"] > current_devices:
                            diff = plan["amount"] - current_plan["amount"]
                            price = math.ceil(diff * (remaining_days / 30.0))
                            buttons.append([InlineKeyboardButton(
                                text=f"⬆️ {plan['name']} ({plan['devices']} устр.) — {price} ₽",
                                callback_data=f"upg:{plan['devices']}"
                            )])
                if buttons:
                    upgrade_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        except Exception:
            logger.exception("Ошибка расчёта апгрейда")

    await message.answer("\n".join(details), reply_markup=upgrade_kb or main_keyboard)

@dp.callback_query(F.data.startswith("upg:"))
async def handle_upgrade_click(callback: CallbackQuery):
    if not platega_is_configured():
        await callback.answer("Оплата не настроена.", show_alert=True)
        return
    
    target_devices = int(callback.data.split(":")[1])
    target_plan = get_tariff_by_devices(target_devices)
    
    sub = await get_subscription(callback.from_user.id)
    if not sub or sub.get("status") != "active":
        await callback.answer("Подписка неактивна.", show_alert=True)
        return
    
    current_plan = get_tariff_by_name(sub.get("tariff"))
    if not current_plan:
        await callback.answer("Текущий тариф не распознан.", show_alert=True)
        return

    expires_at = datetime.fromisoformat(sub["expires_at"])
    remaining_days = (expires_at - datetime.now(timezone.utc)).days
    if remaining_days < 1:
        await callback.answer("До конца подписки меньше дня. Апгрейд недоступен.", show_alert=True)
        return

    diff = target_plan["amount"] - current_plan["amount"]
    price = math.ceil(diff * (remaining_days / 30.0))

    try:
        info = await create_payment(
            user_id=callback.from_user.id, username=callback.from_user.username,
            amount=price, devices=target_devices, days=remaining_days
        )
        await save_payment(
            transaction_id=info["transaction_id"], user_id=callback.from_user.id,
            username=callback.from_user.username, tariff=target_plan["name"],
            devices=target_devices, amount=price, days=remaining_days,
            kind="upgrade", target_devices=target_devices
        )
    except Exception as exc:
        logger.warning("Ошибка создания платежа на апгрейд: %s", type(exc).__name__)
        await callback.message.answer("Не удалось создать платёж. Попробуй позже.")
        await callback.answer()
        return

    await callback.message.answer(
        f"⬆️ Апгрейд до тарифа «{target_plan['name']}»\n\n"
        f"Новых устройств: {target_devices}\n"
        f"Оставшийся срок: {remaining_days} дней (сохранится)\n"
        f"К доплате: {price} ₽\n\n"
        f"Оплати по кнопке ниже. После подтверждения лимит устройств обновится автоматически.",
        reply_markup=payment_keyboard(info["payment_url"], info["transaction_id"])
    )
    await callback.answer()

# --- ПОМОЩЬ ---
@dp.message(F.text == "🆘 Помощь")
async def help_command(message: Message):
    await message.answer("🆘 Помощь по «Воздух VPN»\n\nЕсли возник вопрос, напиши в службу поддержки.\n\nПолезные документы доступны по кнопкам ниже.", reply_markup=help_keyboard)

# --- ФОНОВЫЕ ПРОЦЕССЫ ---
async def check_h1_on_startup():
    try:
        inbounds = await get_inbounds()
        if not inbounds:
            logger.warning("H1 API доступен, но список инбаундов пустой")
            return
        matching = any(str(item.get("id", item.get("inbound_id", ""))) == str(H1_INBOUND_ID) for item in inbounds)
        logger.info("H1 API доступен; инбаундов получено: %s", len(inbounds))
        if not matching:
            logger.warning("H1_INBOUND_ID не найден среди инбаундов панели")
    except H1APIError as exc:
        logger.warning("Проверка H1 API не прошла: %s", type(exc).__name__)

async def payment_monitor(bot: Bot):
    while True:
        try:
            for payment in await get_pending_payments():
                try:
                    result = await process_payment(payment["transaction_id"], int(payment["user_id"]))
                    if result.get("issued_now"):
                        await notify_issued(bot, int(payment["user_id"]), result)
                except (PlategaAPIError, H1APIError) as exc:
                    logger.warning("Платёж пока не обработан: %s", type(exc).__name__)
                except Exception:
                    logger.exception("Ошибка фоновой обработки платежа")
        except Exception:
            logger.exception("Ошибка списка ожидающих платежей")
        await asyncio.sleep(PAYMENT_CHECK_INTERVAL)

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Добавь токен в переменные окружения Bothost.")
    await init_db()
    await check_h1_on_startup()
    bot = Bot(token=BOT_TOKEN)
    monitor = asyncio.create_task(payment_monitor(bot))
    try:
        logger.info("Бот запускается")
        await dp.start_polling(bot)
    finally:
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
