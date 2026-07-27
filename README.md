# ⚡ Instant Dashboard Builder — Serverless Telegram Bot

> Опиши дашборд словами — получи готовый код на **Streamlit**. Работает на **Cloudflare Workers** + **D1**, без единого сервера.

**Instant Dashboard Builder** — это Telegram-бот на бессерверной Edge-архитектуре. Он принимает
текстовое описание дашборда и через **Groq** (`llama-3.3-70b-versatile`) мгновенно генерирует
валидный Python-код для Streamlit со встроенными тестовыми данными. Скопировал, запустил
`streamlit run app.py` — и видишь работающий интерактивный дашборд.

Весь бот целиком живёт в одном Cloudflare Worker: Telegram шлёт апдейты по **Webhook**, воркер
обрабатывает их через **grammY** и мгновенно отвечает. Данные пользователей и лимиты хранятся
в бессерверной SQL-базе **Cloudflare D1**.

---

## ✨ Возможности

- ⚡ **Serverless Edge** — деплой в Cloudflare Workers, холодный старт близок к нулю.
- 🪝 **Webhook-архитектура** — никакого polling: POST от Telegram → grammY → ответ.
- 🧩 **Готовый к запуску код** — Streamlit-файл с мок-данными, внешние источники не нужны.
- 🎛️ **Современный Streamlit API** — `st.metric`, `st.dataframe`, `st.line_chart`,
  `st.bar_chart`, интерактивный `sidebar`.
- 🎁 **Freemium на D1** — 3 бесплатные генерации в сутки, сброс каждые 24 часа.
- 💎 **Premium** — безлимитная генерация после оплаты.
- 💳 **Telegram Payments** — Telegram Stars ⭐ или фиатный провайдер; полная обработка
  `pre_checkout_query` и `successful_payment`.
- 🛡️ **Надёжность** — обработка ошибок сети/Groq, таймауты, защита от спама; воркер не падает.

---

## 🏗️ Технологический стек

| Компонент          | Технология                                   |
|--------------------|----------------------------------------------|
| Язык               | TypeScript (строгая типизация)               |
| Рантайм            | Cloudflare Workers (Wrangler CLI)            |
| Telegram Framework | [grammY](https://grammy.dev)                 |
| База данных        | Cloudflare D1 (serverless SQL)               |
| LLM                | Groq API через нативный `fetch` (`llama-3.3-70b-versatile`) |
| Целевой фреймворк  | Streamlit (для генерируемого кода)           |

---

## 📁 Структура проекта

```
.
├── src/
│   ├── index.ts        # Точка входа Worker: обработчик fetch/webhook
│   ├── bot.ts          # grammY: команды, генерация, инвойсы, платежи
│   ├── db.ts           # Работа с D1: лимиты, сброс, выдача премиума
│   ├── groq.ts         # Запрос к Groq API через fetch + обработка ошибок
│   └── types.ts        # Интерфейсы Env, конфига, строк БД, контекста
├── schema.sql          # SQL для инициализации таблиц в D1
├── wrangler.toml       # Конфиг воркера + биндинг env.DB
├── package.json        # Скрипты wrangler и зависимости
├── tsconfig.json       # Строгая конфигурация TypeScript
├── .dev.vars.example   # Шаблон локальных секретов для `wrangler dev`
└── README.md
```

---

## 🚀 Быстрый старт

### 0. Предварительно

- Аккаунт [Cloudflare](https://dash.cloudflare.com) (бесплатного плана достаточно).
- Node.js 18+ и токен бота от [@BotFather](https://t.me/BotFather).
- Ключ [Groq API](https://console.groq.com/keys).

### 1. Установка зависимостей

```bash
git clone https://github.com/<your-username>/instant-dashboard-builder.git
cd instant-dashboard-builder
npm install
npx wrangler login
```

### 2. Создание базы данных D1

```bash
npx wrangler d1 create instant-dashboard-db
```

Команда выведет блок с `database_id`. **Скопируйте его** в `wrangler.toml`:

```toml
[[d1_databases]]
binding = "DB"
database_name = "instant-dashboard-db"
database_id = "СЮДА_ВСТАВЬТЕ_ВАШ_ID"
```

### 3. Накат миграций (schema.sql)

```bash
# В облачную D1:
npm run db:init
# или напрямую:
npx wrangler d1 execute instant-dashboard-db --remote --file=./schema.sql

# Для локальной разработки (wrangler dev):
npm run db:init:local
```

### 4. Настройка секретов

Несекретные параметры уже лежат в `wrangler.toml` (`[vars]`). Секреты задаются отдельно:

```bash
npx wrangler secret put BOT_TOKEN         # токен от @BotFather
npx wrangler secret put GROQ_API_KEY      # ключ Groq
npx wrangler secret put WEBHOOK_SECRET    # произвольная строка для защиты вебхука
# Только при PAYMENT_PROVIDER=fiat:
npx wrangler secret put PROVIDER_TOKEN    # токен платёжного провайдера
```

| Секрет / переменная | Где задаётся      | Описание                                              |
|---------------------|-------------------|-------------------------------------------------------|
| `BOT_TOKEN`         | `secret put`      | Токен бота Telegram                                   |
| `GROQ_API_KEY`      | `secret put`      | Ключ Groq API                                         |
| `WEBHOOK_SECRET`    | `secret put`      | Секрет для проверки подлинности вебхука (рекомендуется)|
| `PROVIDER_TOKEN`    | `secret put`      | Токен провайдера — **только** для `fiat`              |
| `PAYMENT_PROVIDER`  | `[vars]`          | `stars` (по умолчанию) или `fiat`                     |
| `PREMIUM_PRICE`     | `[vars]`          | Число звёзд (`stars`) или центы (`fiat`)              |
| `PREMIUM_CURRENCY`  | `[vars]`          | Валюта для `fiat` (для Stars всегда `XTR`)            |
| `PREMIUM_DAYS`      | `[vars]`          | Срок подписки в днях                                  |
| `FREE_DAILY_LIMIT`  | `[vars]`          | Бесплатных генераций в сутки                           |
| `MAX_PROMPT_LENGTH` | `[vars]`          | Максимальная длина запроса (антиспам)                 |

> 💡 **Про платежи.** Для **Telegram Stars** ничего подключать не нужно: оставьте
> `PAYMENT_PROVIDER=stars` и не задавайте `PROVIDER_TOKEN`. Для фиатных платежей подключите
> провайдера через @BotFather → *Payments*, поставьте `PAYMENT_PROVIDER=fiat` и добавьте
> секрет `PROVIDER_TOKEN`.

### 5. Деплой

```bash
npm run deploy
# или: npx wrangler deploy
```

Wrangler выведет публичный URL воркера, например:
`https://instant-dashboard-builder.<your-subdomain>.workers.dev`

### 6. Регистрация вебхука в Telegram

Скажите Telegram отправлять апдеты на ваш воркер (подставьте свои значения):

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -d "url=https://instant-dashboard-builder.<your-subdomain>.workers.dev" \
  -d "secret_token=<WEBHOOK_SECRET>"
```

`secret_token` должен совпадать с секретом `WEBHOOK_SECRET`. Готово — пишите боту в Telegram!

---

## 🤖 CI/CD — авто-деплой через GitHub Actions

В репозитории настроен workflow `.github/workflows/deploy.yml`: при каждом пуше в `main`
он ставит зависимости, прогоняет строгую проверку типов и деплоит воркер в Cloudflare.

Чтобы он заработал, добавьте в репозиторий два секрета
(**Settings → Secrets and variables → Actions → New repository secret**):

| Секрет                  | Где взять                                                                 |
|-------------------------|--------------------------------------------------------------------------|
| `CLOUDFLARE_API_TOKEN`  | Cloudflare Dashboard → My Profile → API Tokens → *Edit Cloudflare Workers* |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Dashboard → Workers & Pages (Account ID справа)                |

> Секреты самого бота (`BOT_TOKEN`, `GROQ_API_KEY` и т.д.) в CI не нужны — они живут
> в Cloudflare и задаются через `wrangler secret put` (см. шаг 4). Деплой их не перезаписывает.
> Изменения только в `*.md` и мета-файлах деплой не запускают.

---

## 🧪 Локальная разработка

```bash
cp .dev.vars.example .dev.vars   # заполните секреты для локали
npm run db:init:local            # создать таблицы в локальной D1
npm run dev                      # wrangler dev
npm run typecheck                # проверка строгой типизации
```

Для приёма апдейтов локально пробросьте порт наружу (например, через `cloudflared tunnel`)
и укажите публичный URL туннеля в `setWebhook`.

---

## 💬 Как пользоваться

1. Откройте диалог с ботом и нажмите **/start**.
2. Отправьте описание дашборда, например:
   > *«Дашборд продаж по месяцам: KPI по выручке, график динамики и таблица по регионам с фильтром»*
3. Получите готовый код `app.py` и запустите:

```bash
pip install streamlit pandas numpy
streamlit run app.py
```

4. Бесплатно доступно **3 генерации в сутки**. При исчерпании лимита появится кнопка
   **⭐ Купить Premium (Безлимит)** — после оплаты ограничения снимаются.

---

## 🧠 Как это работает

1. **index.ts** ловит POST-вебхук, проверяет `secret_token` и передаёт апдейт в grammY.
2. **bot.ts** валидирует текст, проверяет лимиты в D1 и запускает генерацию.
3. **db.ts** решает, разрешён ли доступ (дневной лимит / активный Premium),
   и списывает попытку **только после** успешной генерации.
4. **groq.ts** через нативный `fetch` обращается к Groq, обрабатывая таймауты,
   `429` и сетевые ошибки, и извлекает чистый код из markdown-ответа.
5. Ответ отправляется удобным копируемым блоком, а объёмный код — файлом `app.py`.
6. Покупка Premium: `sendInvoice` → `pre_checkout_query` → `successful_payment`,
   после чего статус пользователя в D1 становится `premium`.

---

## ⚠️ Обработка ошибок

- **Groq недоступен / таймаут / rate-limit** — понятное сообщение пользователю,
  бесплатная попытка **не списывается**.
- **Слишком длинный / пустой запрос** — вежливо отклоняется с подсказкой.
- **Любая непойманная ошибка** — воркер логирует её и всё равно отвечает `200`,
  чтобы Telegram не устраивал шторм повторных доставок.
- **Истёкший Premium** — статус автоматически понижается до `free` при следующей проверке.

---

## 📄 Лицензия

Распространяется на условиях лицензии **MIT**. Используйте свободно.
