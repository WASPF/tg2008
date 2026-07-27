-- =========================================================================
--  Instant Dashboard Builder — схема базы данных Cloudflare D1
--
--  Накат миграции:
--    Локально:  npm run db:init:local
--    В облаке:  npm run db:init
--  (или напрямую: wrangler d1 execute instant-dashboard-db --remote --file=./schema.sql)
-- =========================================================================

CREATE TABLE IF NOT EXISTS users (
    -- Telegram user id
    user_id            INTEGER PRIMARY KEY,

    -- Тариф пользователя: 'free' или 'premium'
    status             TEXT    NOT NULL DEFAULT 'free',

    -- Остаток бесплатных генераций в текущих сутках
    free_requests_left INTEGER NOT NULL DEFAULT 3,

    -- Дата окончания Premium-подписки (ISO-8601, UTC) или NULL
    sub_expires_at     TEXT,

    -- Момент последнего сброса дневного лимита (ISO-8601, UTC)
    last_reset         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- Момент создания записи (ISO-8601, UTC)
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Ускоряет выборку истекающих премиум-подписок при необходимости.
CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);
