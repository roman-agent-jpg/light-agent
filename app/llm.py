"""Каскад LLM-провайдеров с автоматическим фолбэком.

Если основной провайдер (Antigravity) не отвечает или падает —
запрос автоматически уходит следующему в цепочке.
"""
import time
from typing import Iterator

from openai import OpenAI

from . import config

# Какие ошибки считаем поводом переключиться на фолбэк
_FAIL_MARKERS = (
    "timeout", "timed out", "connection", "connect", "unavailable",
    "503", "502", "504", "500", "429", "rate limit", "overloaded",
    "service unavailable", "temporarily", "refused", "reset by peer",
)

_stats: dict[str, dict] = {}


def _is_retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _FAIL_MARKERS)


def _record(name: str, ok: bool, elapsed: float) -> None:
    s = _stats.setdefault(name, {"ok": 0, "fail": 0, "last_ms": 0})
    s["ok" if ok else "fail"] += 1
    s["last_ms"] = round(elapsed * 1000)


def stats() -> dict:
    return dict(_stats)


class LLMError(RuntimeError):
    pass


def _client(provider: dict) -> OpenAI:
    kwargs: dict = {
        "api_key": provider["api_key"],
        "base_url": provider["base_url"],
        "timeout": 120.0,
        "max_retries": 1,
    }
    if provider["name"] == "openrouter":
        kwargs["default_headers"] = {
            "HTTP-Referer": "https://agent.onrender.com",
            "X-OpenRouter-Title": "Light Agent",
        }
    return OpenAI(**kwargs)


def chat(messages: list[dict], tools: list[dict] | None = None,
         model: str | None = None, temperature: float = 0.7) -> dict:
    """Один запрос с каскадом фолбэком. Возвращает dict с content и tool_calls."""
    chain = config.providers()
    if not chain:
        raise LLMError("Не настроен ни один LLM-провайдер (AG_API_KEY пуст)")

    errors: list[str] = []
    for provider in chain:
        models = [model] if model and model in provider["models"] else provider["models"]
        for mdl in models:
            try:
                start = time.perf_counter()
                resp = _client(provider).chat.completions.create(
                    model=mdl,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                )
                _record(f"{provider['name']}:{mdl}", True, time.perf_counter() - start)
                msg = resp.choices[0].message
                tool_calls = []
                for tc in (msg.tool_calls or []):
                    import json
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": args,
                        "raw": tc.function.arguments,
                    })
                return {
                    "content": msg.content or "",
                    "tool_calls": tool_calls,
                    "provider": f"{provider['name']}:{mdl}",
                }
            except Exception as exc:
                _record(f"{provider['name']}:{mdl}", False, 0)
                msg = f"{type(exc).__name__}: {exc}"[:200]
                errors.append(f"{provider['name']}:{mdl} -> {msg}")
                if not _is_retryable(exc):
                    break  # ошибка запроса — другие модели не помогут

    raise LLMError("Все провайдеры недоступны:\n" + "\n".join(errors))


def stream(messages: list[dict], model: str | None = None,
           temperature: float = 0.7) -> Iterator[str]:
    """Стриминг текста. Фолбэк переключает провайдера при ошибке до первого токена."""
    chain = config.providers()
    if not chain:
        raise LLMError("Не настроен ни один LLM-провайдер")

    errors: list[str] = []
    for provider in chain:
        models = [model] if model and model in provider["models"] else provider["models"]
        for mdl in models:
            started = False
            try:
                start = time.perf_counter()
                stream_obj = _client(provider).chat.completions.create(
                    model=mdl, messages=messages, temperature=temperature, stream=True,
                )
                for chunk in stream_obj:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        started = True
                        yield delta
                _record(f"{provider['name']}:{mdl}", True, time.perf_counter() - start)
                return
            except Exception as exc:
                _record(f"{provider['name']}:{mdl}", False, 0)
                if started:
                    raise  # уже есть текст — переключение даст обрыв ответа
                errors.append(f"{provider['name']}:{mdl} -> {type(exc).__name__}")
                if not _is_retryable(exc):
                    break

    raise LLMError("Все провайдеры недоступны:\n" + "\n".join(errors))