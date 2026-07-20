/**
 * groq.ts
 * =======
 * Сервис обращения к Groq API через нативный fetch (без SDK — идеально для Edge).
 *
 * Экспортирует:
 *   - SYSTEM_PROMPT      — жёсткая инструкция для модели;
 *   - GroqError          — доменная ошибка генерации;
 *   - generateDashboard  — генерация Streamlit-кода;
 *   - extractCode        — извлечение чистого кода из ответа модели.
 */

const GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions";

/** Таймаут ожидания ответа Groq (мс). */
const REQUEST_TIMEOUT_MS = 55_000;

/** Системный промт: жёстко задаёт формат и качество результата. */
export const SYSTEM_PROMPT = `You are "Instant Dashboard Builder", an expert Python engineer specialised in Streamlit data dashboards. The user gives a plain-text description of the dashboard they want. You output READY-TO-RUN Streamlit code.

STRICT RULES — follow every single one:
1. Output MUST be a single, valid, modern Python file for Streamlit.
2. Use the current Streamlit API: st.set_page_config, st.title, st.header, st.metric, st.dataframe, st.line_chart, st.bar_chart, st.area_chart, st.sidebar, columns via st.columns, etc. Do NOT use deprecated calls.
3. The file MUST embed realistic MOCK/SAMPLE data inside it (build DataFrames with pandas / numpy, use fixed random seeds) so the user can run it immediately with zero external files or databases.
4. Include a sidebar with at least one interactive control (filter, date range, selectbox or slider) that actually affects the displayed data.
5. Show KPI metrics with st.metric where it makes sense.
6. Code must be clean, PEP8-friendly, self-contained and free of syntax errors.
7. Import only widely available libraries: streamlit, pandas, numpy (and datetime from stdlib). Do NOT invent packages.
8. Do NOT include explanations or prose OUTSIDE the code. Return ONLY one Markdown fenced code block starting with \`\`\`python and ending with \`\`\`. No text before or after the block.
9. Assume the file will be saved as \`app.py\`.

Generate the best possible dashboard for the user's request now.`;

/** Ошибка обращения к Groq API (сеть, лимиты, недоступность, пустой ответ). */
export class GroqError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GroqError";
  }
}

/** Минимально необходимая форма ответа Groq (OpenAI-совместимый формат). */
interface GroqChatResponse {
  choices?: Array<{ message?: { content?: string | null } }>;
  error?: { message?: string };
}

/**
 * Извлекает Python-код из ответа модели.
 * Если найден fenced-блок ```python ... ``` — берём его содержимое,
 * иначе возвращаем весь текст (модель могла прислать «голый» код).
 */
export function extractCode(raw: string): string {
  const match = raw.match(/```(?:python|py)?\s*\n([\s\S]*?)```/i);
  return (match ? match[1] : raw).trim();
}

/**
 * Отправляет запрос пользователя в Groq и возвращает сгенерированный
 * Python-код для Streamlit (только код, без markdown-обёртки).
 *
 * Бросает GroqError при любой проблеме: таймаут, сетевая ошибка,
 * не-2xx ответ, некорректный JSON или пустой результат. Worker при этом
 * не падает — вызывающий код ловит GroqError и отвечает пользователю.
 */
export async function generateDashboard(
  apiKey: string,
  model: string,
  userPrompt: string,
): Promise<string> {
  // AbortController гарантирует, что запрос не «зависнет» дольше таймаута.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(GROQ_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        temperature: 0.4,
        max_tokens: 4096,
        top_p: 0.9,
      }),
      signal: controller.signal,
    });
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    throw new GroqError(
      aborted
        ? "Превышено время ожидания ответа. Попробуйте ещё раз."
        : "Не удалось связаться с сервисом генерации. Попробуйте позже.",
    );
  } finally {
    clearTimeout(timer);
  }

  // --- Обработка HTTP-статусов ---
  if (!response.ok) {
    if (response.status === 429) {
      throw new GroqError(
        "Сервис перегружен (превышен лимит запросов). Попробуйте через минуту.",
      );
    }
    throw new GroqError(
      "Сервис генерации вернул ошибку. Попробуйте повторить запрос позже.",
    );
  }

  // --- Разбор тела ответа ---
  let data: GroqChatResponse;
  try {
    data = (await response.json()) as GroqChatResponse;
  } catch {
    throw new GroqError("Получен некорректный ответ от сервиса. Попробуйте ещё раз.");
  }

  const content = data.choices?.[0]?.message?.content;
  if (!content) {
    throw new GroqError(
      "Модель вернула пустой ответ. Попробуйте переформулировать запрос.",
    );
  }

  const code = extractCode(content);
  if (!code) {
    throw new GroqError("Не удалось извлечь код из ответа модели. Попробуйте ещё раз.");
  }

  return code;
}
