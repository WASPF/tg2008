/**
 * index.ts
 * ========
 * Главная точка входа Cloudflare Worker.
 *
 * Telegram доставляет апдейты через Webhook (POST). Worker принимает запрос,
 * передаёт его в grammY через webhookCallback (адаптер "cloudflare-mod")
 * и сразу возвращает ответ. Никакого polling — чистая бессерверная модель.
 */

import { webhookCallback } from "grammy";

import { createBot } from "./bot";
import type { Env } from "./types";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Простой health-check и защита от лишних методов.
    if (request.method !== "POST") {
      return new Response("Instant Dashboard Builder is running. ✅", {
        status: 200,
      });
    }

    try {
      const bot = createBot(env);

      // Адаптер "cloudflare-mod" превращает Request в Response.
      // secretToken сверяется с заголовком X-Telegram-Bot-Api-Secret-Token
      // (задаётся при установке вебхука) — защита от подделки запросов.
      const handleUpdate = webhookCallback(bot, "cloudflare-mod", {
        secretToken: env.WEBHOOK_SECRET,
      });

      return await handleUpdate(request);
    } catch (err) {
      // Никогда не роняем Worker: логируем и отвечаем 200, чтобы Telegram
      // не устраивал шторм повторных доставок из-за 5xx.
      console.error("Worker error:", err);
      return new Response("ok", { status: 200 });
    }
  },
};
