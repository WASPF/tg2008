/**
 * db.ts
 * =====
 * Слой работы с базой данных Cloudflare D1.
 *
 * Отвечает за:
 *   - создание записи пользователя при первом обращении;
 *   - проверку доступа с учётом дневного лимита и премиум-статуса;
 *   - автоматический сброс лимита каждые 24 часа;
 *   - списание одной генерации;
 *   - выдачу/продление Premium после успешной оплаты.
 *
 * Все временные метки хранятся в UTC в формате ISO-8601.
 */

import type { AccessResult, UserRow } from "./types";

/** Интервал сброса бесплатных лимитов — 24 часа в миллисекундах. */
const RESET_PERIOD_MS = 24 * 60 * 60 * 1000;

/** Текущее время в ISO-8601 (UTC). */
function nowIso(): string {
  return new Date().toISOString();
}

/** Безопасный разбор ISO-строки в миллисекунды. Возвращает null при ошибке. */
function parseMs(value: string | null): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Гарантирует наличие записи о пользователе. Новому пользователю выдаётся
 * полный дневной лимит бесплатных генераций. Повторные вызовы безопасны.
 */
export async function ensureUser(
  db: D1Database,
  userId: number,
  freeLimit: number,
): Promise<void> {
  const ts = nowIso();
  await db
    .prepare(
      `INSERT INTO users (user_id, status, free_requests_left, sub_expires_at, last_reset, created_at)
       VALUES (?1, 'free', ?2, NULL, ?3, ?3)
       ON CONFLICT(user_id) DO NOTHING`,
    )
    .bind(userId, freeLimit, ts)
    .run();
}

/** Возвращает строку пользователя или null. */
async function getUser(db: D1Database, userId: number): Promise<UserRow | null> {
  return db
    .prepare("SELECT * FROM users WHERE user_id = ?1")
    .bind(userId)
    .first<UserRow>();
}

/**
 * Проверяет, может ли пользователь сделать запрос к Groq.
 *
 * Логика:
 *   1. Гарантируем наличие пользователя.
 *   2. Premium активен -> доступ без ограничений.
 *   3. Premium истёк -> понижаем до free.
 *   4. Прошло >= 24ч с последнего сброса -> восстанавливаем дневной лимит.
 *   5. Разрешаем доступ, если остались бесплатные генерации.
 *
 * Списание НЕ производится (см. consumeRequest).
 */
export async function checkAccess(
  db: D1Database,
  userId: number,
  freeLimit: number,
): Promise<AccessResult> {
  await ensureUser(db, userId, freeLimit);

  let user = await getUser(db, userId);
  if (!user) {
    // Теоретически недостижимо после ensureUser, но соблюдаем строгую типизацию.
    return { allowed: false, isPremium: false, requestsLeft: 0 };
  }

  const now = Date.now();

  // --- 1. Проверка Premium ---
  if (user.status === "premium") {
    const expires = parseMs(user.sub_expires_at);
    if (expires !== null && expires > now) {
      return { allowed: true, isPremium: true, requestsLeft: -1 };
    }
    // Подписка истекла -> понижаем статус до free.
    await db
      .prepare(
        "UPDATE users SET status = 'free', sub_expires_at = NULL WHERE user_id = ?1",
      )
      .bind(userId)
      .run();
    user = { ...user, status: "free", sub_expires_at: null };
  }

  // --- 2. Сброс дневного лимита при необходимости ---
  const lastReset = parseMs(user.last_reset) ?? now;
  let requestsLeft = user.free_requests_left;
  if (now - lastReset >= RESET_PERIOD_MS) {
    requestsLeft = freeLimit;
    await db
      .prepare(
        "UPDATE users SET free_requests_left = ?2, last_reset = ?3 WHERE user_id = ?1",
      )
      .bind(userId, freeLimit, nowIso())
      .run();
  }

  // --- 3. Итоговое решение ---
  return {
    allowed: requestsLeft > 0,
    isPremium: false,
    requestsLeft,
  };
}

/**
 * Списывает одну бесплатную генерацию у free-пользователя.
 * Для активного Premium возвращает -1 и ничего не списывает.
 *
 * Условие `free_requests_left > 0` в UPDATE не даёт балансу уйти в минус
 * даже при гонке одновременных запросов.
 *
 * Возвращает остаток бесплатных генераций (или -1 для premium).
 */
export async function consumeRequest(
  db: D1Database,
  userId: number,
): Promise<number> {
  const user = await getUser(db, userId);
  if (!user) return 0;

  if (user.status === "premium") {
    const expires = parseMs(user.sub_expires_at);
    if (expires !== null && expires > Date.now()) {
      return -1;
    }
  }

  await db
    .prepare(
      "UPDATE users SET free_requests_left = free_requests_left - 1 WHERE user_id = ?1 AND free_requests_left > 0",
    )
    .bind(userId)
    .run();

  const updated = await getUser(db, userId);
  return updated ? updated.free_requests_left : 0;
}

/**
 * Выдаёт (или продлевает) Premium-подписку после успешной оплаты.
 * Если подписка ещё активна — новый срок добавляется к остатку.
 *
 * Возвращает новую дату окончания (ISO-8601).
 */
export async function grantPremium(
  db: D1Database,
  userId: number,
  days: number,
  freeLimit: number,
): Promise<string> {
  await ensureUser(db, userId, freeLimit);

  const user = await getUser(db, userId);
  const now = Date.now();
  const currentExpires = parseMs(user?.sub_expires_at ?? null);

  // Продлеваем от текущей даты окончания, если она в будущем, иначе от «сейчас».
  const base = currentExpires !== null && currentExpires > now ? currentExpires : now;
  const newExpires = new Date(base + days * RESET_PERIOD_MS).toISOString();

  await db
    .prepare(
      "UPDATE users SET status = 'premium', sub_expires_at = ?2 WHERE user_id = ?1",
    )
    .bind(userId, newExpires)
    .run();

  return newExpires;
}
