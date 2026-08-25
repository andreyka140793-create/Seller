"""Общий вызов Gemini с перебором рабочих моделей."""
from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

# Порядок: сначала актуальные Flash (2026), затем запасные.
# gemini-2.5-flash у части ключей уже отдаёт 404.
DEFAULT_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


def get_model_candidates() -> list[str]:
    preferred = os.getenv("GEMINI_MODEL", "").strip()
    models: list[str] = []
    if preferred:
        models.append(preferred)
    for m in DEFAULT_MODELS:
        if m not in models:
            models.append(m)
    return models


def generate_json(client, prompt: str, response_schema=None, temperature: float = 0.0):
    """
    generate_content с JSON. Перебирает модели при 404/NOT_FOUND.
    Возвращает text ответа или None.
    """
    from google.genai import types

    config_kwargs: dict = {
        "response_mime_type": "application/json",
        "temperature": temperature,
    }
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    config = types.GenerateContentConfig(**config_kwargs)
    last_error: Exception | None = None

    for model in get_model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            text = getattr(response, "text", None)
            if text:
                logger.info("Gemini OK, model=%s", model)
                return text
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            # пробуем следующую модель при 404 / not found / unavailable
            if any(x in msg for x in ("404", "not found", "not_found", "not supported", "deprecated")):
                logger.warning("Gemini model %s failed: %s — try next", model, e)
                continue
            logger.warning("Gemini model %s error: %s", model, e)
            continue

    if last_error:
        logger.error("All Gemini models failed: %s", last_error)
    return None
