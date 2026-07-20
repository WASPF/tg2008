"""
utils.py
========
Вспомогательные функции: формирование системного промта и обращение
к Groq API с надёжной обработкой ошибок сети и API.

Публичные объекты:
  * SYSTEM_PROMPT       — жёсткая инструкция для LLM.
  * GroqError           — доменное исключение для проблем с генерацией.
  * generate_dashboard  — асинхронная генерация Streamlit-кода.
  * extract_code        — извлечение чистого Python-кода из ответа модели.
"""

from __future__ import annotations

import asyncio
import logging
import re

from groq import (
    APIConnectionError,
    APIStatusError,
    AsyncGroq,
    RateLimitError,
)

from config import settings

logger = logging.getLogger(__name__)

# Таймаут ожидания ответа Groq (сек).
_REQUEST_TIMEOUT = 60.0

# Ленивая инициализация клиента: создаём его при первом обращении, а не на
# импорте модуля. Это ускоряет старт, не делает сетевых приготовлений во время
# импорта и упрощает тестирование.
_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    """Возвращает singleton-клиент AsyncGroq, создавая его при первом вызове."""
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key, timeout=_REQUEST_TIMEOUT)
    return _client


# --------------------------------------------------------------------------- #
#  Системный промт
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """\
You are "Instant Dashboard Builder", an expert Python engineer specialised in \
Streamlit data dashboards. The user gives a plain-text description of the \
dashboard they want. You output READY-TO-RUN Streamlit code.

STRICT RULES — follow every single one:
1. Output MUST be a single, valid, modern Python file for Streamlit.
2. Use the current Streamlit API: st.set_page_config, st.title, st.header, \
st.metric, st.dataframe, st.line_chart, st.bar_chart, st.area_chart, \
st.sidebar, columns via st.columns, etc. Do NOT use deprecated calls.
3. The file MUST embed realistic MOCK/SAMPLE data inside it (build DataFrames \
with pandas / numpy, use fixed random seeds) so the user can run it \
immediately with zero external files or databases.
4. Include a sidebar with at least one interactive control (filter, date \
range, selectbox or slider) that actually affects the displayed data.
5. Show KPI metrics with st.metric where it makes sense.
6. Code must be clean, PEP8-friendly, self-contained and free of syntax errors.
7. Import only widely available libraries: streamlit, pandas, numpy \
(and datetime from stdlib). Do NOT invent packages.
8. Do NOT include explanations, comments to the user, or prose OUTSIDE the \
code. Return ONLY one Markdown fenced code block starting with ```python and \
ending with ```. No text before or after the block.
9. Assume the file will be saved as `app.py`.

Generate the best possible dashboard for the user's request now.
"""


class GroqError(Exception):
    """Ошибка обращения к Groq API (сеть, лимиты, недоступность и т.п.)."""


# --------------------------------------------------------------------------- #
#  Извлечение кода из ответа модели
# --------------------------------------------------------------------------- #
_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(raw: str) -> str:
    """
    Извлекает Python-код из ответа модели.

    Если модель вернула fenced-блок ```python ... ``` — берём его содержимое.
    Иначе возвращаем весь текст (модель могла прислать «голый» код).
    """
    match = _CODE_BLOCK_RE.search(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()


# --------------------------------------------------------------------------- #
#  Генерация дашборда
# --------------------------------------------------------------------------- #
async def generate_dashboard(user_prompt: str) -> str:
    """
    Отправляет запрос пользователя в Groq и возвращает сгенерированный
    Python-код для Streamlit (только код, без markdown-обёртки).

    Бросает GroqError при любых проблемах: недоступность сети, превышение
    лимитов Groq, ошибочный ответ API или пустой результат.
    """
    try:
        completion = await _get_client().chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,        # немного детерминизма для стабильного кода
            max_tokens=4096,
            top_p=0.9,
        )
    except RateLimitError as exc:
        logger.warning("Groq rate limit: %s", exc)
        raise GroqError(
            "Сервис перегружен (превышен лимит запросов). "
            "Попробуйте, пожалуйста, через минуту."
        ) from exc
    except APIConnectionError as exc:
        logger.error("Groq connection error: %s", exc)
        raise GroqError(
            "Не удалось связаться с сервисом генерации. "
            "Проверьте подключение и попробуйте ещё раз."
        ) from exc
    except APIStatusError as exc:
        logger.error("Groq API status error %s: %s", exc.status_code, exc)
        raise GroqError(
            "Сервис генерации вернул ошибку. Попробуйте повторить запрос позже."
        ) from exc
    except asyncio.TimeoutError as exc:
        logger.error("Groq timeout: %s", exc)
        raise GroqError(
            "Превышено время ожидания ответа. Попробуйте ещё раз."
        ) from exc
    except Exception as exc:  # noqa: BLE001 — защитная сеть последнего уровня
        logger.exception("Неожиданная ошибка Groq: %s", exc)
        raise GroqError(
            "Произошла непредвиденная ошибка при генерации. Попробуйте позже."
        ) from exc

    # Проверяем содержимое ответа.
    if not completion.choices or not completion.choices[0].message.content:
        raise GroqError("Модель вернула пустой ответ. Попробуйте переформулировать запрос.")

    code = extract_code(completion.choices[0].message.content)
    if not code:
        raise GroqError("Не удалось извлечь код из ответа модели. Попробуйте ещё раз.")

    return code
