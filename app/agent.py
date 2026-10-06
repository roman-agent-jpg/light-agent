"""Цикл агента: вызов LLM, выполнение инструментов, итерации до ответа.

Ключевые решения:
- Работает в отдельном потоке, потому что LLM-вызов блокирующий. Иначе один
  долгий запрос вешает весь сервер на 30+ секунд.
- Поддерживает отмену: пользователь может прервать задачу.
- Отдаёт события прогресса через колбэк, чтобы интерфейс показывал,
  что агент делает прямо сейчас.
"""
import threading
import time
from concurrent.futures import Future as _Future
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, llm, memory, tools

MAX_STEPS = 20           # защита от бесконечного цикла вызовов инструментов
MAX_TOOL_OUTPUT = 8000   # сколько символов вывода инструмента уходит модели

_step_counter = {"count": 0}
_last_activity = {"ts": time.time()}
_active_cancel: dict[str, threading.Event] = {}


def touch() -> None:
    """Отмечает активность — используется anti-sleep таймером."""
    _last_activity["ts"] = time.time()
    _step_counter["count"] = 0


def idle_seconds() -> float:
    return time.time() - _last_activity["ts"]


def state() -> dict:
    return {
        "idle_seconds": round(idle_seconds()),
        "tools_used": _step_counter["count"],
        "running_jobs": len(_active_cancel),
    }


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def cancel(session_id: str) -> bool:
    """Просит текущую задачу сессии остановиться."""
    ev = _active_cancel.get(session_id)
    if ev is None:
        return False
    ev.set()
    _log(f"cancel requested for {session_id}")
    return True


def _build_messages(session_id: str, user_message: str,
                    history: list[dict] | None, project: str = "default") -> list[dict]:
    """Собирает контекст для LLM.

    Сводка о прошлом вклеивается в ТОТ ЖЕ системный промпт. Два
    system-сообщения подряд модель читает плохо: отвечает "OK" на первый
    попавшийся вопрос, а не на последний.
    """
    system = tools.SYSTEM_PROMPT
    messages: list[dict] = []

    if history is not None:
        for m in history[-24:]:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": user_message})
    else:
        # build_context сам добавляет вопрос последним элементом — режем его.
        built = memory.build_context(session_id, user_message, project=project)
        if built and built[0]["role"] == "system":
            system += "\n\n" + built[0]["content"]
            fresh = built[1:-1]
        else:
            fresh = built[:-1]
        for m in fresh:
            messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": user_message})

    messages.insert(0, {"role": "system", "content": system})
    return messages


def run(user_message: str, session_id: str = "default",
        history: list[dict] | None = None,
        on_event=None, cancel_event: threading.Event | None = None,
        project: str = "default", model: str | None = None) -> dict:
    """Выполняет задачу и возвращает финальный ответ.

    on_event(kind: str, payload: dict) вызывается по ходу работы:
      kind="step" — номер итерации
      kind="tool" — модель вызвала инструмент
    """
    if cancel_event is None:
        cancel_event = _active_cancel.setdefault(
            session_id, threading.Event())

    def emit(kind: str, payload: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(kind, payload)
        except Exception:
            pass  # ошибка интерфейса не должна ломать задачу

    # Изолируем рабочую директорию проекта для текущего потока агента
    config.set_current_project(project)
    touch()
    messages = _build_messages(session_id, user_message, history, project=project)
    memory.add_message(session_id, "user", user_message, project=project)

    tool_specs = tools.build_tools()
    steps: list[str] = []
    provider_used = ""
    total_start = time.perf_counter()

    try:
        for step in range(MAX_STEPS):
            if cancel_event.is_set():
                return {
                    "ok": False, "cancelled": True,
                    "error": "Задача отменена пользователем",
                    "answer": "⏹ Задача отменена пользователем.",
                    "steps": steps, "provider": provider_used,
                    "elapsed": round(time.perf_counter() - total_start, 2),
                }

            emit("step", {"index": step + 1, "max": MAX_STEPS})

            try:
                resp = llm.chat(messages, tools=tool_specs, model=model)
            except llm.LLMError as exc:
                _log(f"LLM unavailable: {exc}")
                return {
                    "ok": False, "error": str(exc),
                    "answer": f"⚠️ Ошибка сервиса модели: {exc}",
                    "steps": steps,
                    "elapsed": round(time.perf_counter() - total_start, 2),
                }

            provider_used = resp["provider"]

            if not resp["tool_calls"]:
                answer = (resp["content"] or "").strip() or "Готово."
                memory.add_message(session_id, "assistant", answer, project=project)
                _log(f"done in {round(time.perf_counter() - total_start, 2)}s "
                     f"via {provider_used}, steps: {step}")
                return {
                    "ok": True, "answer": answer, "steps": steps,
                    "provider": provider_used,
                    "elapsed": round(time.perf_counter() - total_start, 2),
                }

            messages.append({
                "role": "assistant",
                "content": resp["content"] or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": tc["raw"] or "{}"}}
                    for tc in resp["tool_calls"]
                ],
            })

            for tc in resp["tool_calls"]:
                if cancel_event.is_set():
                    break
                preview = ", ".join(
                    f"{k}={str(v)[:40]}" for k, v in list(tc["args"].items())[:3])
                emit("tool", {"name": tc["name"], "args": tc["args"],
                              "preview": preview})
                _log(f"tool {tc['name']}({preview})")

                output = tools.execute(tc["name"], tc["args"])
                trimmed = output[:MAX_TOOL_OUTPUT]
                if len(output) > MAX_TOOL_OUTPUT:
                    trimmed += (f"\n...[обрезано, было {len(output)} символов]")

                steps.append(f"{tc['name']}: {output[:200]}")
                _step_counter["count"] += 1
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": trimmed,
                })

        err_msg = f"Превышен лимит шагов ({MAX_STEPS})."
        recent = ("\nПоследние выполненные действия:\n" + "\n".join(f"- {s}" for s in steps[-4:])) if steps else ""
        return {
            "ok": False,
            "error": err_msg,
            "answer": f"⚠️ {err_msg}{recent}",
            "steps": steps, "provider": provider_used,
            "elapsed": round(time.perf_counter() - total_start, 2),
        }
    finally:
        # Убираем токен отмены, чтобы сессия не копила мусор
        _active_cancel.pop(session_id, None)


def run_stream(user_message: str, session_id: str = "default",
               history: list[dict] | None = None, project: str = "default",
               model: str | None = None):
    """Стриминг текста без инструментов — быстрый режим для вопросов."""
    config.set_current_project(project)
    touch()
    messages = _build_messages(session_id, user_message, history, project=project)

    full: list[str] = []
    try:
        for chunk in llm.stream(messages, model=model):
            full.append(chunk)
            yield chunk
    except llm.LLMError as exc:
        yield f"\n\n[Ошибка: {exc}]"
        return

    answer = "".join(full).strip()
    if answer:
        memory.add_message(session_id, "assistant", answer, project=project)


# Пул из одного потока: не даёт съесть память Render free (512 МБ).
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent")


def submit(session_id: str, message: str, history=None,
           on_event=None, project: str = "default",
           model: str | None = None) -> "Job":
    """Запускает задачу в отдельном потоке."""
    ev = threading.Event()
    _active_cancel[session_id] = ev
    fut = _pool.submit(run, message, session_id, history, on_event, ev, project, model)
    return Job(fut, session_id)


class Job:
    """Обёртка над Future с отменой по сессии."""

    def __init__(self, fut: _Future, session_id: str):
        self._fut = fut
        self._session_id = session_id

    def done(self) -> bool:
        return self._fut.done()

    def result(self, timeout: float | None = None) -> dict:
        return self._fut.result(timeout=timeout)

    def cancel(self) -> bool:
        return cancel(self._session_id)