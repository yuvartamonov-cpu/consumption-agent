"""Shared text-generation routing with provider fallback.

Order is always OpenAI -> Gemini -> xAI on each request.
If OpenAI quota/billing/auth recovers, it is automatically used first again.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

log = logging.getLogger(__name__)

OPENAI_TEXT_MODEL = os.getenv("TEXT_MODEL_OPENAI", "gpt-4o-mini")
GEMINI_TEXT_MODEL = os.getenv("TEXT_MODEL_GEMINI", "gemini-2.5-flash")
XAI_TEXT_MODEL = os.getenv("TEXT_MODEL_XAI", "grok-4.20")


def _usage_dict(*, input_tokens: int | None = None, output_tokens: int | None = None) -> dict[str, int]:
    usage: dict[str, int] = {}
    if input_tokens is not None:
        usage["input"] = int(input_tokens)
    if output_tokens is not None:
        usage["output"] = int(output_tokens)
    return usage


def _provider_error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if not text and hasattr(exc, "response") and exc.response is not None:
        try:
            text = exc.response.text
        except Exception:
            text = exc.__class__.__name__
    return text or exc.__class__.__name__


def _should_fallback(exc: Exception) -> bool:
    text = _provider_error_message(exc).lower()
    markers = (
        "insufficient_quota",
        "quota",
        "billing",
        "429",
        "rate limit",
        "rate_limit",
        "resource_exhausted",
        "too many requests",
        "authentication",
        "unauthorized",
        "permission denied",
        "api key not valid",
        "invalid api key",
    )
    return any(marker in text for marker in markers)


def _extract_xai_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    output = getattr(response, "output", None) or []
    for item in output:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                return text.strip()
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        for item in dumped.get("output", []) or []:
            for content in item.get("content", []) or []:
                text = content.get("text")
                if text:
                    return str(text).strip()
    raise RuntimeError("xAI returned an empty text response")


def call_openai_text(
    *,
    system_prompt: str | None,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 300,
    temperature: float = 0.1,
) -> dict[str, Any]:
    import openai

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    chosen_model = model or OPENAI_TEXT_MODEL

    client = openai.OpenAI(api_key=api_key, timeout=20.0)
    response = client.chat.completions.create(
        model=chosen_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    usage = getattr(response, "usage", None)
    return {
        "provider": "openai",
        "model": chosen_model,
        "text": response.choices[0].message.content.strip(),
        "usage": _usage_dict(
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        ),
    }


def call_gemini_text(
    *,
    system_prompt: str | None,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 300,
    temperature: float = 0.1,
    response_mime_type: str = "text/plain",
) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY/GOOGLE_API_KEY not set")

    chosen_model = model or GEMINI_TEXT_MODEL
    parts: list[dict[str, str]] = []
    if system_prompt:
        parts.append({"text": system_prompt})
    parts.append({"text": user_prompt})
    payload = {
        "contents": [{
            "parts": parts,
        }],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": response_mime_type,
        },
    }
    req = urllib_request.Request(
        url=f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API: HTTP {exc.code} {body}") from exc

    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {body}")
    parts_out = candidates[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts_out if part.get("text")).strip()
    if not text:
        raise RuntimeError(f"Gemini returned no text: {body}")
    usage_meta = body.get("usageMetadata") or {}
    return {
        "provider": "gemini",
        "model": chosen_model,
        "text": text,
        "usage": _usage_dict(
            input_tokens=usage_meta.get("promptTokenCount"),
            output_tokens=usage_meta.get("candidatesTokenCount"),
        ),
    }


def call_xai_text(
    *,
    system_prompt: str | None,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 300,
) -> dict[str, Any]:
    import openai

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set")

    content = []
    if system_prompt:
        content.append({"type": "input_text", "text": system_prompt})
    content.append({"type": "input_text", "text": user_prompt})
    chosen_model = model or XAI_TEXT_MODEL

    client = openai.OpenAI(api_key=api_key, base_url="https://api.x.ai/v1", timeout=30.0)
    response = client.responses.create(
        model=chosen_model,
        input=[{"role": "user", "content": content}],
        max_output_tokens=max_tokens,
    )
    usage = getattr(response, "usage", None)
    return {
        "provider": "xai",
        "model": chosen_model,
        "text": _extract_xai_text(response),
        "usage": _usage_dict(
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        ),
    }


def call_text_with_fallback(
    *,
    system_prompt: str | None,
    user_prompt: str,
    openai_model: str | None = None,
    gemini_model: str | None = None,
    xai_model: str | None = None,
    max_tokens: int = 300,
    temperature: float = 0.1,
    response_mime_type: str = "text/plain",
) -> dict[str, Any]:
    attempts: list[str] = []
    last_error: Exception | None = None
    providers = (
        ("openai", lambda: call_openai_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=openai_model,
            max_tokens=max_tokens,
            temperature=temperature,
        )),
        ("gemini", lambda: call_gemini_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=gemini_model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_mime_type=response_mime_type,
        )),
        ("xai", lambda: call_xai_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=xai_model,
            max_tokens=max_tokens,
        )),
    )

    for provider_name, runner in providers:
        try:
            result = runner()
            usage = result.get("usage") or {}
            if usage:
                log.info(
                    "Text LLM provider=%s model=%s tokens=%s+%s",
                    result["provider"],
                    result["model"],
                    usage.get("input", "?"),
                    usage.get("output", "?"),
                )
            else:
                log.info("Text LLM provider=%s model=%s", result["provider"], result["model"])
            return result
        except Exception as exc:
            last_error = exc
            attempts.append(f"{provider_name}: {_provider_error_message(exc)}")
            if _should_fallback(exc):
                log.warning("Text provider %s failed, falling back: %s", provider_name, exc)
                continue
            log.warning("Text provider %s failed without fallback: %s", provider_name, exc)
            if provider_name != "xai":
                continue

    raise RuntimeError("All text providers failed: " + " | ".join(attempts)) from last_error
