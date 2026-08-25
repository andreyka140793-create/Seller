"""
LLM для разбора структуры прайса — только Grok (xAI).
Gemini отключён. Без ключа работает эвристика колонок.
"""
from __future__ import annotations

import base64
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


def generate_json(prompt: str, temperature: float = 0.0) -> str | None:
    """Текст → JSON через Grok. None, если нет ключа или ошибка."""
    if not os.getenv("XAI_API_KEY"):
        logger.info("XAI_API_KEY не задан — LLM пропущен, только эвристика")
        return None
    return _call_grok(prompt, temperature=temperature, images=None)


def generate_json_from_image(
    prompt: str,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    temperature: float = 0.0,
) -> str | None:
    """Картинка/скриншот прайса → JSON через Grok Vision."""
    if not os.getenv("XAI_API_KEY"):
        return None
    return _call_grok(prompt, temperature=temperature, images=[(image_bytes, mime_type)])


def _extract_json(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            json.loads(m.group(1))
            return m.group(1)
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            json.loads(m.group(0))
            return m.group(0)
        except json.JSONDecodeError:
            pass
    return None


def _call_grok(
    prompt: str,
    temperature: float,
    images: list[tuple[bytes, str]] | None,
) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("pip install openai")
        return None

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        return None

    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    models: list[str] = []
    preferred = os.getenv("GROK_MODEL", os.getenv("XAI_MODEL", "")).strip()
    if preferred:
        models.append(preferred)
    models.extend([m for m in GROK_MODELS if m not in models])

    system = (
        "Ты анализируешь прайс-листы (таблицы, текст, скриншоты). "
        "Отвечай ТОЛЬКО валидным JSON без markdown и пояснений."
    )

    if images:
        content: list | str = [{"type": "text", "text": prompt}]
        for img_bytes, mime in images:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        user_msg = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": prompt}

    for model in models:
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    user_msg,
                ],
            )
            content_out = resp.choices[0].message.content or ""
            extracted = _extract_json(content_out)
            if extracted:
                logger.info("Grok OK, model=%s", model)
                return extracted
            logger.warning("Grok model %s: invalid JSON", model)
        except Exception as e:
            logger.warning("Grok model %s failed: %s", model, e)
            continue
    return None
