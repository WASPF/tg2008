"""
handlers.py
===========
Обработчики Telegram-бота (aiogram Router).

Реализует:
  * /start, /help — приветствие и справка;
  * приём текстовых запросов и генерацию Streamlit-дашбордов;
  * лимиты freemium + inline-кнопку покупки Premium;
  * платёжный флоу: инвойс -> pre_checkout_query -> successful_payment.
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import database as db
from config import settings
from utils import GroqError, generate_dashboard

logger = logging.getLogger(__name__)

router = Router(name="main")

# Уникальный payload инвойса — по нему сверяем платёж в successful_payment.
_PREMIUM_PAYLOAD = "premium_subscription"

# Лимит длины одного сообщения Telegram (символов).
_TG_MSG_LIMIT = 4096


# --------------------------------------------------------------------------- #
#  Клавиатуры
# --------------------------------------------------------------------------- #
def _buy_premium_keyboard() -> InlineKeyboardMarkup:
    """Inline-клавиатура с кнопкой покупки Premium."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Купить Premium (Безлимит)",
                    callback_data="buy_premium",
                )
            ]
        ]
    )


# --------------------------------------------------------------------------- #
#  Отправка длинного кода
# --------------------------------------------------------------------------- #
async def _send_code(message: Message, code: str, requests_left: int, is_premium: bool) -> None:
    """
    Отправляет сгенерированный код пользователю удобным для копирования способом.

    Стратегия:
      * Короткий код — одним HTML <pre><code> блоком: в Telegram такой блок
        копируется одним тапом по кнопке «Copy».
      * Длинный код (не помещается в одно сообщение) — отправляется готовым
        файлом `app.py`. Его можно сразу скачать и запустить, а копировать
        целиком удобнее, чем несколько разбитых сообщений.
    """
    run_hint = (
        "🚀 <b>Как запустить:</b>\n"
        "<pre>pip install streamlit pandas numpy\n"
        "streamlit run app.py</pre>"
    )

    if is_premium:
        footer = "💎 Статус: <b>Premium</b> — безлимитная генерация."
    else:
        footer = f"📊 Осталось бесплатных генераций сегодня: <b>{requests_left}</b>"

    # Проверяем, помещается ли код в одно сообщение с учётом HTML-обёртки,
    # заголовка, подсказки по запуску и футера.
    escaped = html.escape(code)
    header = "✅ <b>Ваш дашборд готов!</b>\n\n"
    inline_message = (
        f"{header}"
        f'<pre><code class="language-python">{escaped}</code></pre>\n\n'
        f"{run_hint}\n\n{footer}"
    )

    if len(inline_message) <= _TG_MSG_LIMIT:
        # Короткий код — показываем копируемым блоком прямо в чате.
        await message.answer(inline_message)
        return

    # Длинный код — отдаём файлом app.py, готовым к запуску.
    document = BufferedInputFile(code.encode("utf-8"), filename="app.py")
    caption = (
        f"{header}"
        "Код получился объёмным, поэтому отправляю его файлом "
        "<code>app.py</code> — сохраните и запустите.\n\n"
        f"{run_hint}\n\n{footer}"
    )
    await message.answer_document(document, caption=caption)


# --------------------------------------------------------------------------- #
#  Команды /start и /help
# --------------------------------------------------------------------------- #
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Приветствие и регистрация пользователя."""
    await db.ensure_user(message.from_user.id)
    text = (
        "👋 <b>Привет! Это Instant Dashboard Builder.</b>\n\n"
        "Опишите словами, какой дашборд вам нужен — а я мгновенно сгенерирую "
        "готовый Python-код на <b>Streamlit</b> с тестовыми данными, который "
        "можно сразу запустить локально.\n\n"
        "<b>Примеры запросов:</b>\n"
        "• «Дашборд продаж по месяцам с фильтром по регионам»\n"
        "• «Аналитика посещаемости сайта: метрики, график трафика, таблица источников»\n"
        "• «Финансовый дашборд с KPI и графиком доходов/расходов»\n\n"
        f"🎁 Бесплатно: <b>{settings.free_daily_limit} генерации в день</b>.\n"
        "Команда /help — подробная справка."
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка по использованию бота."""
    text = (
        "ℹ️ <b>Как пользоваться ботом</b>\n\n"
        "1️⃣ Просто отправьте текстовое описание нужного дашборда.\n"
        "2️⃣ Бот вернёт готовый файл <code>app.py</code> для Streamlit.\n"
        "3️⃣ Сохраните код, установите зависимости и запустите:\n"
        "<pre>pip install streamlit pandas numpy\n"
        "streamlit run app.py</pre>\n\n"
        f"🎁 <b>Free-тариф:</b> {settings.free_daily_limit} генерации в сутки "
        "(сбрасывается каждые 24 часа).\n"
        "💎 <b>Premium:</b> безлимитная генерация. "
        "Кнопка покупки появится при исчерпании лимита.\n\n"
        "Команды: /start — начало, /help — эта справка."
    )
    await message.answer(text)


# --------------------------------------------------------------------------- #
#  Основной обработчик генерации
# --------------------------------------------------------------------------- #
@router.message(F.text & ~F.text.startswith("/"))
async def handle_generation(message: Message) -> None:
    """Приём текстового запроса и генерация дашборда с учётом лимитов."""
    user_id = message.from_user.id
    prompt = (message.text or "").strip()

    # --- Защита от пустого / слишком короткого запроса ---
    if len(prompt) < 3:
        await message.answer(
            "✍️ Опишите, пожалуйста, нужный дашборд чуть подробнее "
            "(минимум несколько слов)."
        )
        return

    # --- Защита от спама слишком длинным текстом ---
    if len(prompt) > settings.max_prompt_length:
        await message.answer(
            "⚠️ Запрос слишком длинный "
            f"(максимум {settings.max_prompt_length} символов). "
            "Сформулируйте описание короче и по существу."
        )
        return

    # --- Проверка доступа / лимитов ---
    access = await db.check_access(user_id)
    if not access.allowed:
        await message.answer(
            "🚫 <b>Дневной лимит бесплатных генераций исчерпан.</b>\n\n"
            "Лимит обновится в течение 24 часов, либо оформите "
            "<b>Premium</b> для безлимитного доступа прямо сейчас.",
            reply_markup=_buy_premium_keyboard(),
        )
        return

    # --- Генерация ---
    status_msg = await message.answer("⏳ Генерирую дашборд, это займёт пару секунд...")
    try:
        code = await generate_dashboard(prompt)
    except GroqError as exc:
        # Ошибка API — лимит НЕ списываем, пользователь не теряет попытку.
        await status_msg.edit_text(f"⚠️ {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Непредвиденная ошибка генерации: %s", exc)
        await status_msg.edit_text(
            "⚠️ Что-то пошло не так при генерации. Попробуйте ещё раз чуть позже."
        )
        return

    # Успех — списываем одну генерацию (для premium вернётся -1).
    requests_left = await db.consume_request(user_id)

    # Удаляем «⏳ Генерирую...» и отправляем результат.
    try:
        await status_msg.delete()
    except Exception:  # noqa: BLE001 — не критично, если удалить не удалось
        pass

    await _send_code(
        message,
        code=code,
        requests_left=max(requests_left, 0),
        is_premium=access.is_premium or requests_left == -1,
    )


# --------------------------------------------------------------------------- #
#  Покупка Premium
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "buy_premium")
async def on_buy_premium(callback: CallbackQuery) -> None:
    """Отправляет инвойс на оплату Premium-подписки."""
    await callback.answer()

    title = "Premium подписка — Instant Dashboard Builder"
    description = (
        f"Безлимитная генерация дашбордов на {settings.premium_days} дней. "
        "Никаких дневных ограничений!"
    )

    # Для Telegram Stars валюта XTR, amount = число звёзд, provider_token = "".
    # Для фиатного провайдера amount указывается в минимальных единицах (центах).
    prices = [LabeledPrice(label="Premium", amount=settings.premium_price)]

    try:
        await callback.message.answer_invoice(
            title=title,
            description=description,
            payload=_PREMIUM_PAYLOAD,
            provider_token=settings.provider_token,  # "" для Stars
            currency=settings.currency,              # "XTR" для Stars
            prices=prices,
            start_parameter="premium",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось выставить инвойс: %s", exc)
        await callback.message.answer(
            "⚠️ Не удалось создать счёт на оплату. "
            "Попробуйте позже или обратитесь к администратору."
        )


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    """
    Подтверждает готовность принять платёж.

    Telegram требует ответить на pre_checkout_query в течение 10 секунд,
    иначе платёж будет отклонён.
    """
    is_valid = pre_checkout.invoice_payload == _PREMIUM_PAYLOAD
    await pre_checkout.answer(
        ok=is_valid,
        error_message=None if is_valid else "Некорректный счёт. Попробуйте оформить заново.",
    )


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    """
    Обрабатывает успешную оплату: выдаёт/продлевает Premium-статус в БД.
    """
    payment = message.successful_payment

    # Дополнительная проверка payload на всякий случай.
    if payment.invoice_payload != _PREMIUM_PAYLOAD:
        logger.warning(
            "Успешный платёж с неизвестным payload: %s", payment.invoice_payload
        )
        await message.answer(
            "✅ Оплата получена, но возникла ошибка сверки. "
            "Свяжитесь с поддержкой — мы всё исправим."
        )
        return

    expires = await db.grant_premium(message.from_user.id)
    expires_str = expires.strftime("%d.%m.%Y")

    logger.info(
        "Успешная оплата от %s (%s %s)",
        message.from_user.id,
        payment.total_amount,
        payment.currency,
    )

    await message.answer(
        "🎉 <b>Оплата прошла успешно! Спасибо!</b>\n\n"
        "💎 Ваш статус: <b>Premium</b>\n"
        f"📅 Действует до: <b>{expires_str}</b>\n\n"
        "Теперь генерация дашбордов безлимитна. Просто присылайте описание!"
    )
