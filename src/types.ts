/**
 * types.ts
 * ========
 * Строгие типы окружения Cloudflare, разобранной конфигурации,
 * строк базы данных и расширенного контекста grammY.
 */

import type { Context } from "grammy";

/**
 * Env — переменные окружения и биндинги Cloudflare Worker.
 *
 * Секреты (BOT_TOKEN, GROQ_API_KEY, PROVIDER_TOKEN, WEBHOOK_SECRET) задаются
 * через `wrangler secret put`. Остальные значения приходят из [vars]
 * wrangler.toml и всегда представлены строками.
 */
export interface Env {
  /** Биндинг базы данных Cloudflare D1. */
  DB: D1Database;

  // --- Секреты ---
  BOT_TOKEN: string;
  GROQ_API_KEY: string;
  /** Токен фиатного платёжного провайдера (пусто/undefined при оплате Stars). */
  PROVIDER_TOKEN?: string;
  /** Секрет для проверки заголовка X-Telegram-Bot-Api-Secret-Token (опционально). */
  WEBHOOK_SECRET?: string;

  // --- Публичные переменные (строки из [vars]) ---
  GROQ_MODEL?: string;
  PAYMENT_PROVIDER?: string;
  PREMIUM_PRICE?: string;
  PREMIUM_CURRENCY?: string;
  PREMIUM_DAYS?: string;
  FREE_DAILY_LIMIT?: string;
  MAX_PROMPT_LENGTH?: string;
}

/** Способ приёма оплаты. */
export type PaymentProvider = "stars" | "fiat";

/**
 * BotConfig — конфигурация, уже разобранная из Env в удобные типы
 * (числа, enum). Формируется один раз на запрос в loadConfig().
 */
export interface BotConfig {
  groqApiKey: string;
  groqModel: string;
  paymentProvider: PaymentProvider;
  providerToken: string;
  premiumPrice: number;
  /** Код валюты инвойса: "XTR" для Stars, иначе фиатная валюта. */
  currency: string;
  premiumDays: number;
  freeDailyLimit: number;
  maxPromptLength: number;
}

/** Строка таблицы users в том виде, как её возвращает D1. */
export interface UserRow {
  user_id: number;
  status: "free" | "premium";
  free_requests_left: number;
  sub_expires_at: string | null;
  last_reset: string;
  created_at: string;
}

/** Результат проверки доступа пользователя к генерации. */
export interface AccessResult {
  /** Разрешено ли обращение к Groq. */
  allowed: boolean;
  /** Активна ли премиум-подписка. */
  isPremium: boolean;
  /** Остаток бесплатных генераций (для premium не используется). */
  requestsLeft: number;
}

/**
 * AppContext — контекст grammY, расширенный ссылками на окружение Worker
 * и разобранную конфигурацию. Заполняется middleware на каждый апдейт.
 */
export type AppContext = Context & {
  env: Env;
  config: BotConfig;
};
