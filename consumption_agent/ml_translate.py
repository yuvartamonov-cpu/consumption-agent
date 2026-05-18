"""
ml_translate.py — Перевод поисковых запросов через LLM.

Заменяет словарный перевод (QUERY_TRANSLATIONS) в ml_providers.py на
осмысленный перевод с учётом полного контекста Memory Lane.

Flow:
  1. Собирается контекст: name, brand, description, style_tags, caption,
     subcategory, category, material, primary_color, fit, gender, season
  2. OpenAI (GPT-4o-mini) получает этот контекст и возвращает осмысленный
     перевод на заданный язык (en, de, kk, kz, etc.)
  3. Если вызов OpenAI не удался — fallback на словарный перевод.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

_QUERY_TRANSLATIONS: dict[str, str] | None = None
_EN_TO_DE: dict[str, str] | None = None


_TRANSLATE_SYSTEM_PROMPT = (
    "Ты переводчик поисковых запросов для интернет-магазинов. "
    "Пользователь (русскоязычный) ищет товар по текстовому описанию. "
    "Твоя задача — перевести запрос на указанный язык максимально "
    "естественно для поиска на маркетплейсах, сохраняя все значимые "
    "слова (бренд, модель, артикул, материал, цвет, размер, тип). "
    "Если бренд или модель уже на латинице — не меняй их. "
    "Ответь ТОЛЬКО текстом запроса, без пояснений, без кавычек."
)

_TRANSLATE_USER_TEMPLATE = """
Контекст товара:
- Название: {name}
- Бренд: {brand}
- Категория: {category}
- Подкатегория: {subcategory}
- Описание: {description}
- Стили: {style_tags}
- Цвет: {color}
- Материал: {material}
- Крой/посадка: {fit}
- Пол: {gender}
- Сезон: {season}

Исходный поисковый запрос (на русском):
{query}

Переведи этот запрос на язык "{target_lang}" для поиска товара на маркетплейсе. 
Сохрани бренды и модели как есть, если они уже на латинице.
Только текст запроса, ничего лишнего.
"""


def _init_fallback() -> None:
    global _QUERY_TRANSLATIONS, _EN_TO_DE
    try:
        import ml_providers  # type: ignore[import-untyped]
        _QUERY_TRANSLATIONS = getattr(ml_providers, 'QUERY_TRANSLATIONS', {})
        _EN_TO_DE = getattr(ml_providers, 'EN_TO_DE', {})
    except ImportError:
        _QUERY_TRANSLATIONS = {}
        _EN_TO_DE = {}


def _has_openai_key() -> bool:
    return bool(os.getenv('OPENAI_API_KEY'))


def _call_llm_translate(
    query: str,
    target_lang: str,
    context: dict[str, Any] | None = None,
) -> str | None:
    if not _has_openai_key():
        return None

    user_prompt = _TRANSLATE_USER_TEMPLATE.format(
        name=(context or {}).get('name', '—') or '—',
        brand=(context or {}).get('brand', '—') or '—',
        category=(context or {}).get('category', '—') or '—',
        subcategory=(context or {}).get('subcategory', '—') or '—',
        description=(context or {}).get('description', '—') or '—',
        style_tags=', '.join((context or {}).get('style_tags', [])) or '—',
        color=(context or {}).get('primary_color', '—') or '—',
        material=(context or {}).get('material', '—') or '—',
        fit=(context or {}).get('fit', '—') or '—',
        gender=(context or {}).get('gender', '—') or '—',
        season=(context or {}).get('season', '—') or '—',
        query=query,
        target_lang=target_lang,
    )

    try:
        import openai
        client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'),
                               timeout=12.0)
        response = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': _TRANSLATE_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=150,
            temperature=0.1,
        )
        result = response.choices[0].message.content.strip()
        result = result.strip('"\'«»')
        if not result:
            return None
        log.info(
            "ml_translate: LLM %s->%s: %r -> %r",
            'ru', target_lang, query[:80], result[:80],
        )
        return result
    except Exception as e:
        log.warning("ml_translate: LLM translate failed: %s", e)
        return None


# -----------------------------------------------------------------------
# Словарный fallback
# -----------------------------------------------------------------------

_QUERY_WORD_RX = re.compile(r"[\wА-Яа-яЁё][\wА-Яа-яЁё-]*", re.UNICODE)
_CYRILLIC_RX = re.compile(r'[А-Яа-яЁё]')
_LATIN_RX = re.compile(r'[A-Za-z]')
_RU_ADJ_SUFFIXES = (
    'ые', 'ие', 'ый', 'ий', 'ой', 'ая', 'яя', 'ое', 'ее',
    'ых', 'их', 'ым', 'им', 'ую', 'юю', 'ого', 'его',
    'ому', 'ему', 'ой', 'ей',
    'ённый', 'енный', 'ённая', 'ённое', 'ённые',
    'нный', 'нная', 'нное', 'нные',
)


def _stem_lookup(word: str) -> str | None:
    if _QUERY_TRANSLATIONS is None:
        _init_fallback()
    w = word.lower()
    val = (_QUERY_TRANSLATIONS or {}).get(w)
    if val is not None:
        return val
    for suf in _RU_ADJ_SUFFIXES:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            stem = w[:-len(suf)]
            for try_suf in ('ый', 'ий', 'ой', 'ая', 'ое', ''):
                val = (_QUERY_TRANSLATIONS or {}).get(stem + try_suf)
                if val is not None:
                    return val
            stem_alt = stem.replace('ё', 'е')
            for try_suf in ('ый', 'ий', 'ой', 'ая', 'ое', ''):
                val = (_QUERY_TRANSLATIONS or {}).get(stem_alt + try_suf)
                if val is not None:
                    return val
    return None


def _dict_translate_to_english(query: str) -> str:
    if not query:
        return query

    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        t = _stem_lookup(word)
        return t if t is not None else word

    translated = _QUERY_WORD_RX.sub(repl, query)
    return re.sub(r"\s+", " ", translated).strip() or query


def _dict_en_to_locale(text: str, lang: str) -> str:
    if not text or lang == 'en':
        return text
    if _EN_TO_DE is None:
        _init_fallback()
    if lang == 'de':
        out = text
        en_to_de = _EN_TO_DE or {}
        for src, dst in sorted(en_to_de.items(),
                                key=lambda kv: len(kv[0]), reverse=True):
            out = re.sub(rf'(?<!\\w){re.escape(src)}(?!\\w)', dst, out,
                         flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", out).strip() or text
    return text


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------


def translate_query(
    query: str,
    target_lang: str,
    context: dict[str, Any] | None = None,
    *,
    use_llm: bool = True,
) -> str:
    """Переводит запрос на указанный язык.

    Args:
        query: Исходный запрос (на русском)
        target_lang: Целевой язык ('en', 'de', 'ru')
        context: Контекст товара из Memory Lane
        use_llm: Пытаться LLM или только словарь

    Returns:
        Переведённый запрос
    """
    if not query:
        return query

    if target_lang in ('ru', 'kz', 'kk'):
        return query

    if use_llm:
        llm_result = _call_llm_translate(query, target_lang, context)
        if llm_result:
            return llm_result

    english = _dict_translate_to_english(query)
    if target_lang == 'en':
        return english
    return _dict_en_to_locale(english, target_lang)


def translate_query_with_item(
    query: str,
    target_lang: str,
    item_context: dict[str, Any] | None = None,
    attrs: dict[str, Any] | None = None,
) -> str:
    """Переводит запрос с объединённым контекстом item + attrs."""
    merged: dict[str, Any] = {}
    if item_context:
        merged.update(item_context)
    if attrs:
        merged.update(attrs)
    return translate_query(query, target_lang, context=merged)


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RX.search(text))


def drop_cyrillic(text: str) -> str:
    tokens = _QUERY_WORD_RX.findall(text or '')
    kept = [t for t in tokens if not _CYRILLIC_RX.search(t)]
    return ' '.join(kept).strip()


def drop_non_latin(text: str) -> str:
    tokens = _QUERY_WORD_RX.findall(text or '')
    kept = [t for t in tokens if _LATIN_RX.search(t)]
    return ' '.join(kept).strip()
