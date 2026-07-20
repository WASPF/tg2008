"""
database.py
===========
Асинхронный слой работы с SQLite (через aiosqlite).

Отвечает за:
  * инициализацию таблицы пользователей;
  * учёт и автоматический сброс дневных лимитов бесплатных пользователей;
  * проверку доступа (free/premium) перед генерацией кода;
  * списание лимита после успешной генерации;
  * выдачу и продление Premium-статуса после оплаты.

Все временные метки хранятся в UTC в формате ISO-8601.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

# Интервал сброса бесплатных лимитов
_RESET_PERIOD = timedelta(hours=24)


# --------------------------------------------------------------------------- #
#  DTO — результат проверки доступа
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class AccessResult:
    """Результат проверки права пользователя на генерацию кода."""

    allowed: bool          # можно ли обращаться к Groq
    is_premium: bool       # активна ли премиум-подписка
    requests_left: int     # остаток бесплатных генераций (для free-юзеров)


def _utcnow() -> datetime:
    """Текущее время в UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    """Безопасный разбор ISO-строки в timezone-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        # На случай «наивных» дат из старых записей — приводим к UTC.
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
#  Инициализация схемы
# --------------------------------------------------------------------------- #
async def init_db() -> None:
    """Создаёт таблицу пользователей, если она ещё не существует."""
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id            INTEGER PRIMARY KEY,
                status             TEXT    NOT NULL DEFAULT 'free',
                sub_expires_at     TEXT,
                free_requests_left INTEGER NOT NULL DEFAULT 0,
                last_reset         TEXT    NOT NULL,
                created_at         TEXT    NOT NULL
            )
            """
        )
        await db.commit()
    logger.info("База данных инициализирована: %s", settings.db_path)


# --------------------------------------------------------------------------- #
#  Работа с пользователем
# --------------------------------------------------------------------------- #
async def _get_user_row(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row | None:
    """Возвращает строку пользователя или None."""
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
        return await cursor.fetchone()


async def ensure_user(user_id: int) -> None:
    """
    Гарантирует наличие записи о пользователе.
    Новому пользователю выдаётся полный дневной лимит бесплатных генераций.
    """
    now_iso = _utcnow().isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO users (
                user_id, status, sub_expires_at,
                free_requests_left, last_reset, created_at
            )
            VALUES (?, 'free', NULL, ?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, settings.free_daily_limit, now_iso, now_iso),
        )
        await db.commit()


async def check_access(user_id: int) -> AccessResult:
    """
    Проверяет, может ли пользователь сделать запрос к Groq.

    Логика:
      1. Гарантируем наличие пользователя.
      2. Если Premium активен — доступ без ограничений.
      3. Если Premium истёк — понижаем до free.
      4. Если с последнего сброса прошло >= 24ч — восстанавливаем дневной лимит.
      5. Разрешаем доступ, если остались бесплатные генерации.

    Списание НЕ производится — только проверка (см. consume_request).
    """
    await ensure_user(user_id)
    now = _utcnow()

    async with aiosqlite.connect(settings.db_path) as db:
        row = await _get_user_row(db, user_id)
        assert row is not None  # гарантировано ensure_user

        # --- 1. Проверка Premium ---
        if row["status"] == "premium":
            expires = _parse_dt(row["sub_expires_at"])
            if expires and expires > now:
                return AccessResult(allowed=True, is_premium=True, requests_left=-1)
            # Подписка истекла -> понижаем статус.
            logger.info("Premium пользователя %s истёк, понижаем до free.", user_id)
            await db.execute(
                "UPDATE users SET status = 'free', sub_expires_at = NULL "
                "WHERE user_id = ?",
                (user_id,),
            )
            await db.commit()
            row = await _get_user_row(db, user_id)
            assert row is not None

        # --- 2. Сброс дневного лимита при необходимости ---
        last_reset = _parse_dt(row["last_reset"]) or now
        requests_left = row["free_requests_left"]
        if now - last_reset >= _RESET_PERIOD:
            requests_left = settings.free_daily_limit
            await db.execute(
                "UPDATE users SET free_requests_left = ?, last_reset = ? "
                "WHERE user_id = ?",
                (requests_left, now.isoformat(), user_id),
            )
            await db.commit()
            logger.debug("Сброшен дневной лимит для пользователя %s.", user_id)

        # --- 3. Итоговое решение ---
        return AccessResult(
            allowed=requests_left > 0,
            is_premium=False,
            requests_left=requests_left,
        )


async def consume_request(user_id: int) -> int:
    """
    Списывает одну бесплатную генерацию у free-пользователя.

    Для premium-пользователей ничего не списывает.
    Возвращает остаток бесплатных генераций (для premium вернёт -1).

    Списание защищено условием `free_requests_left > 0`, поэтому
    даже при гонке запросов баланс не уйдёт в минус.
    """
    async with aiosqlite.connect(settings.db_path) as db:
        row = await _get_user_row(db, user_id)
        if row is None:
            return 0

        # Premium — без списания.
        if row["status"] == "premium":
            expires = _parse_dt(row["sub_expires_at"])
            if expires and expires > _utcnow():
                return -1

        await db.execute(
            "UPDATE users SET free_requests_left = free_requests_left - 1 "
            "WHERE user_id = ? AND free_requests_left > 0",
            (user_id,),
        )
        await db.commit()

        row = await _get_user_row(db, user_id)
        return row["free_requests_left"] if row else 0


async def grant_premium(user_id: int, days: int | None = None) -> datetime:
    """
    Выдаёт (или продлевает) Premium-подписку после успешной оплаты.

    Если у пользователя уже есть активная подписка — новый срок
    добавляется к оставшемуся. Возвращает новую дату окончания.
    """
    await ensure_user(user_id)
    days = days or settings.premium_days
    now = _utcnow()

    async with aiosqlite.connect(settings.db_path) as db:
        row = await _get_user_row(db, user_id)
        current_expires = _parse_dt(row["sub_expires_at"]) if row else None

        # Продлеваем от текущей даты окончания, если она ещё в будущем.
        base = current_expires if current_expires and current_expires > now else now
        new_expires = base + timedelta(days=days)

        await db.execute(
            "UPDATE users SET status = 'premium', sub_expires_at = ? "
            "WHERE user_id = ?",
            (new_expires.isoformat(), user_id),
        )
        await db.commit()

    logger.info("Пользователю %s выдан Premium до %s", user_id, new_expires.isoformat())
    return new_expires
