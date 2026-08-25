"""
Единый LLM-клиент для разбора структуры прайса.
Провайдеры: grok (xAI) | gemini (Google).
Эвристика колонок работает и без LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

GROK_MODELS = (
    "grok-4.6",
    "grok-4.5",
    "grok-4.3",
    "grok-3-mini",
)

GEMINI_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


def _provider() -> str:
    # LLM_PROVIDER=grok|gemini  (по умолчанию grok, если есть XAI_API_KEY)
    p = os.getenv("LLM_PROVIDER", "").strip().lower()
    if p in ("grok", "xai", "gemini", "google"):
        return "grok" if p in ("grok", "xai") else "gemini"
    if os.getenv("XAI_API_KEY"):
        return "grok"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "none"


def generate_json(prompt: str, temperature: float = 0.0) -> str | None:
    """
    Возвращает JSON-строку или None.
    Порядок: выбранный провайдер → при ошибке другой (если ключ есть) → None.
    """
    order = []
    primary = _provider()
    if primary == "grok":
        order = ["grok", "gemini"]
    elif primary == "gemini":
        order = ["gemini", "grok"]
    else:
        order = ["grok", "gemini"]

    for name in order:
        if name == "grok" and os.getenv("XAI_API_KEY"):
            text = _call_grok(prompt, temperature)
            if text:
                return text
        if name == "gemini" and os.getenv("GEMINI_API_KEY"):
            text = _call_gemini(prompt, temperature)
            if text:
                return text
    return None


def _extract_json(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    # чистый JSON
    if text.startswith("{"):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
    # блок ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            json.loads(m.group(1))
            return m.group(1)
        except json.JSONDecodeError:
            pass
    # первый { ... }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            json.loads(m.group(0))
            return m.group(0)
        except json.JSONDecodeError:
            pass
    return None


def _call_grok(prompt: str, temperature: float) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("Пакет openai не установлен (pip install openai)")
        return None

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return None

    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    models = []
    preferred = os.getenv("GROK_MODEL", os.getenv("XAI_MODEL", "")).strip()
    if preferred:
        models.append(preferred)
    models.extend([m for m in GROK_MODELS if m not in models])

    system = (
        "Ты анализируешь таблицы прайс-листов. "
        "Отвечай ТОЛЬКО валидным JSON без markdown и пояснений."
    )

    for model in models:
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            content = resp.choices[0].message.content or ""
            extracted = _extract_json(content)
            if extracted:
                logger.info("Grok OK, model=%s", model)
                return extracted
            logger.warning("Grok model %s: empty/invalid JSON", model)
        except Exception as e:
            msg = str(e).lower()
            logger.warning("Grok model %s failed: %s", model, e)
            if any(x in msg for x in ("404", "not found", "does not exist", "invalid model")):
                continue
            continue
    return None


def _call_gemini(prompt: str, temperature: float) -> str | None:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("Пакет google-genai не установлен")
        return None

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)
    models = []
    preferred = os.getenv("GEMINI_MODEL", "").strip()
    if preferred:
        models.append(preferred)
    models.extend([m for m in GEMINI_MODELS if m not in models])

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=temperature,
    )

    for model in models:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            text = getattr(response, "text", None) or ""
            extracted = _extract_json(text)
            if extracted:
                logger.info("Gemini OK, model=%s", model)
                return extracted
        except Exception as e:
            msg = str(e).lower()
            logger.warning("Gemini model %s failed: %s", model, e)
            if any(x in msg for x in ("404", "not found", "not_found", "deprecated")):
                continue
            continue
    return None
