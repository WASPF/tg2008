/**
 * bot.ts
 * ======
 * Инициализация grammY и вся бизнес-логика бота:
 *   - команды /start и /help;
 *   - приём текстовых запросов и генерация дашбордов с учётом лимитов;
 *   - freemium-ограничения + inline-кнопка покупки Premium;
 *   - платёжный флоу: инвойс -> pre_checkout_query -> successful_payment.
 *
 * Бот создаётся заново на каждый запрос Worker'а (stateless-окружение Edge),
 * поэтому вся конфигурация и env прокидываются в контекст через middleware.
 */

import { Bot, InlineKeyboard, InputFile } from "grammy";

import { checkAccess, consumeRequest, grantPremium } from "./db";
import { generateDashboard, GroqError } from "./groq";
import type { AppContext, BotConfig, Env, PaymentProvider } from "./types";

/** Уникальный payload инвойса — по нему сверяем платёж. */
const PREMIUM_PAYLOAD = "premium_subscription";

/** Лимит длины одного сообщения Telegram (символов). */
const TG_MSG_LIMIT = 4096;

/** Дефолтная модель Groq. */
const DEFAULT_MODEL = "llama-3.3-70b-versatile";

// --------------------------------------------------------------------------- #
//  Конфигурация
// --------------------------------------------------------------------------- #

/** Парсит положительное число из строки env, иначе возвращает значение по умолчанию. */
function toPositiveInt(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}

/** Парсит неотрицательное число (для лимита, который может быть 0). */
function toNonNegativeInt(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
}

/** Формирует строго типизированную конфигурацию из «сырого» env. */
export function loadConfig(env: Env): BotConfig {
  const provider: PaymentProvider = env.PAYMENT_PROVIDER === "fiat" ? "fiat" : "stars";
  return {
    groqApiKey: env.GROQ_API_KEY,
    groqModel: env.GROQ_MODEL || DEFAULT_MODEL,
    paymentProvider: provider,
    providerToken: env.PROVIDER_TOKEN ?? "",
    premiumPrice: toPositiveInt(env.PREMIUM_PRICE, 100),
    currency: provider === "stars" ? "XTR" : env.PREMIUM_CURRENCY || "USD",
    premiumDays: toPositiveInt(env.PREMIUM_DAYS, 30),
    freeDailyLimit: toNonNegativeInt(env.FREE_DAILY_LIMIT, 3),
    maxPromptLength: toPositiveInt(env.MAX_PROMPT_LENGTH, 1500),
  };
}

// --------------------------------------------------------------------------- #
//  Вспомогательные функции представления
// --------------------------------------------------------------------------- #

/** Экранирует спецсимволы HTML для безопасной вставки в parse_mode=HTML. */
function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Inline-клавиатура с кнопкой покупки Premium. */
function buyPremiumKeyboard(): InlineKeyboard {
  return new InlineKeyboard().text("⭐ Купить Premium (Безлимит)", "buy_premium");
}

const RUN_HINT =
  "🚀 <b>Как запустить:</b>\n" +
  "<pre>pip install streamlit pandas numpy\n" +
  "streamlit run app.py</pre>";

/**
 * Отправляет сгенерированный код удобным для копирования способом:
 *   - короткий код — одним HTML-блоком (копируется одним тапом в Telegram);
 *   - объёмный код — готовым файлом app.py, который сразу можно запустить.
 */
async function sendGeneratedCode(
  ctx: AppContext,
  code: string,
  requestsLeft: number,
  isPremium: boolean,
): Promise<void> {
  const header = "✅ <b>Ваш дашборд готов!</b>";
  const footer = isPremium
    ? "💎 Статус: <b>Premium</b> — безлимитная генерация."
    : `📊 Осталось бесплатных генераций сегодня: <b>${Math.max(requestsLeft, 0)}</b>`;

  const inline =
    `${header}\n\n` +
    `<pre><code class="language-python">${escapeHtml(code)}</code></pre>\n\n` +
    `${RUN_HINT}\n\n${footer}`;

  if (inline.length <= TG_MSG_LIMIT) {
    await ctx.reply(inline, { parse_mode: "HTML" });
    return;
  }

  // Объёмный код — отдаём файлом app.py.
  const file = new InputFile(new TextEncoder().encode(code), "app.py");
  const caption =
    `${header}\n\n` +
    "Код получился объёмным — отправляю файлом <code>app.py</code>, " +
    "сохраните и запустите.\n\n" +
    `${RUN_HINT}\n\n${footer}`;
  await ctx.replyWithDocument(file, { caption, parse_mode: "HTML" });
}

// --------------------------------------------------------------------------- #
//  Фабрика бота
// --------------------------------------------------------------------------- #

/**
 * Создаёт и полностью настраивает экземпляр бота grammY.
 * Вызывается на каждый входящий запрос Worker'а.
 */
export function createBot(env: Env): Bot<AppContext> {
  const config = loadConfig(env);
  const bot = new Bot<AppContext>(env.BOT_TOKEN);

  // Прокидываем окружение и конфигурацию в контекст каждого апдейта.
  bot.use(async (ctx, next) => {
    ctx.env = env;
    ctx.config = config;
    await next();
  });

  // --- Команды ---
  bot.command("start", async (ctx) => {
    if (ctx.from) {
      await checkAccess(env.DB, ctx.from.id, config.freeDailyLimit); // регистрирует пользователя
    }
    await ctx.reply(
      "👋 <b>Привет! Это Instant Dashboard Builder.</b>\n\n" +
        "Опишите словами, какой дашборд вам нужен — и я мгновенно сгенерирую " +
        "готовый Python-код на <b>Streamlit</b> с тестовыми данными, который " +
        "можно сразу запустить локально.\n\n" +
        "<b>Примеры запросов:</b>\n" +
        "• «Дашборд продаж по месяцам с фильтром по регионам»\n" +
        "• «Аналитика посещаемости сайта: метрики, график трафика, таблица источников»\n" +
        "• «Финансовый дашборд с KPI и графиком доходов/расходов»\n\n" +
        `🎁 Бесплатно: <b>${config.freeDailyLimit} генерации в день</b>.\n` +
        "Команда /help — подробная справка.",
      { parse_mode: "HTML" },
    );
  });

  bot.command("help", async (ctx) => {
    await ctx.reply(
      "ℹ️ <b>Как пользоваться ботом</b>\n\n" +
        "1️⃣ Отправьте текстовое описание нужного дашборда.\n" +
        "2️⃣ Бот вернёт готовый файл <code>app.py</code> для Streamlit.\n" +
        "3️⃣ Сохраните код и запустите:\n" +
        "<pre>pip install streamlit pandas numpy\n" +
        "streamlit run app.py</pre>\n\n" +
        `🎁 <b>Free-тариф:</b> ${config.freeDailyLimit} генерации в сутки ` +
        "(сбрасывается каждые 24 часа).\n" +
        "💎 <b>Premium:</b> безлимитная генерация. Кнопка покупки появится " +
        "при исчерпании лимита.\n\n" +
        "Команды: /start — начало, /help — эта справка.",
      { parse_mode: "HTML" },
    );
  });

  // --- Основной обработчик генерации ---
  bot.on("message:text", async (ctx) => {
    const text = ctx.message.text.trim();
    // Прочие команды (начинаются с «/») игнорируем — их ловят command-хендлеры.
    if (text.startsWith("/")) return;

    const userId = ctx.from?.id;
    const chatId = ctx.chatId;
    if (userId === undefined || chatId === undefined) return;

    if (text.length < 3) {
      await ctx.reply(
        "✍️ Опишите, пожалуйста, нужный дашборд чуть подробнее (минимум несколько слов).",
      );
      return;
    }

    if (text.length > config.maxPromptLength) {
      await ctx.reply(
        `⚠️ Запрос слишком длинный (максимум ${config.maxPromptLength} символов). ` +
          "Сформулируйте описание короче и по существу.",
      );
      return;
    }

    // Проверка лимитов.
    const access = await checkAccess(env.DB, userId, config.freeDailyLimit);
    if (!access.allowed) {
      await ctx.reply(
        "🚫 <b>Дневной лимит бесплатных генераций исчерпан.</b>\n\n" +
          "Лимит обновится в течение 24 часов, либо оформите <b>Premium</b> " +
          "для безлимитного доступа прямо сейчас.",
        { parse_mode: "HTML", reply_markup: buyPremiumKeyboard() },
      );
      return;
    }

    // Генерация.
    const status = await ctx.reply("⏳ Генерирую дашборд, это займёт пару секунд...");
    let code: string;
    try {
      code = await generateDashboard(config.groqApiKey, config.groqModel, text);
    } catch (err) {
      // Ошибка Groq — лимит НЕ списываем, пользователь не теряет попытку.
      const msg =
        err instanceof GroqError
          ? err.message
          : "Что-то пошло не так при генерации. Попробуйте ещё раз чуть позже.";
      await ctx.api
        .editMessageText(chatId, status.message_id, `⚠️ ${msg}`)
        .catch(() => undefined);
      return;
    }

    // Успех — списываем одну генерацию (для premium вернётся -1).
    const requestsLeft = await consumeRequest(env.DB, userId);
    await ctx.api.deleteMessage(chatId, status.message_id).catch(() => undefined);
    await sendGeneratedCode(
      ctx,
      code,
      requestsLeft,
      access.isPremium || requestsLeft === -1,
    );
  });

  // --- Покупка Premium: показ инвойса ---
  bot.callbackQuery("buy_premium", async (ctx) => {
    await ctx.answerCallbackQuery();

    const title = "Premium подписка — Instant Dashboard Builder";
    const description =
      `Безлимитная генерация дашбордов на ${config.premiumDays} дней. ` +
      "Никаких дневных ограничений!";
    const prices = [{ label: "Premium", amount: config.premiumPrice }];

    // Для Stars provider_token не передаётся (валюта XTR).
    // Для фиатного провайдера токен обязателен.
    const other =
      config.paymentProvider === "fiat"
        ? { provider_token: config.providerToken }
        : {};

    try {
      await ctx.replyWithInvoice(
        title,
        description,
        PREMIUM_PAYLOAD,
        config.currency,
        prices,
        other,
      );
    } catch (err) {
      console.error("Invoice error:", err);
      await ctx.reply(
        "⚠️ Не удалось создать счёт на оплату. Попробуйте позже или обратитесь к администратору.",
      );
    }
  });

  // --- Подтверждение платежа (нужно ответить в течение 10 секунд) ---
  bot.on("pre_checkout_query", async (ctx) => {
    const isValid = ctx.preCheckoutQuery.invoice_payload === PREMIUM_PAYLOAD;
    await ctx.answerPreCheckoutQuery(
      isValid,
      isValid
        ? undefined
        : { error_message: "Некорректный счёт. Попробуйте оформить заново." },
    );
  });

  // --- Успешная оплата: выдаём Premium ---
  bot.on("message:successful_payment", async (ctx) => {
    const payment = ctx.message.successful_payment;
    const userId = ctx.from?.id;

    if (payment.invoice_payload !== PREMIUM_PAYLOAD || userId === undefined) {
      await ctx.reply(
        "✅ Оплата получена, но возникла ошибка сверки. Свяжитесь с поддержкой — мы всё исправим.",
      );
      return;
    }

    const expiresIso = await grantPremium(
      env.DB,
      userId,
      config.premiumDays,
      config.freeDailyLimit,
    );
    const expiresStr = new Date(expiresIso).toLocaleDateString("ru-RU");

    await ctx.reply(
      "🎉 <b>Оплата прошла успешно! Спасибо!</b>\n\n" +
        "💎 Ваш статус: <b>Premium</b>\n" +
        `📅 Действует до: <b>${expiresStr}</b>\n\n` +
        "Теперь генерация дашбордов безлимитна. Просто присылайте описание!",
      { parse_mode: "HTML" },
    );
  });

  return bot;
}
