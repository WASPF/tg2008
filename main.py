"""
main.py
=======
Точка входа приложения.

Инициализирует базу данных, создаёт объекты Bot и Dispatcher,
подключает роутер с обработчиками и запускает асинхронный long-polling.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
from config import settings
from handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Асинхронная инициализация и запуск бота."""
    # 1. Готовим базу данных.
    await db.init_db()

    # 2. Создаём бота. HTML — parse_mode по умолчанию для всех сообщений.
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # 3. Диспетчер и регистрация роутеров.
    dp = Dispatcher()
    dp.include_router(router)

    # 4. Логируем, кто мы, и стартуем polling.
    me = await bot.get_me()
    logger.info("Бот @%s запущен. Модель Groq: %s", me.username, settings.groq_model)

    try:
        # Пропускаем накопившиеся во время простоя апдейты.
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен, сессия закрыта.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка по сигналу пользователя.")
