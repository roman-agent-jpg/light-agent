"""Anti-sleep: не даём бесплатному Render-серверу уснуть.

Три уровня защиты:
1. Внутренний keepalive — фоновый таймер пингует собственный публичный URL.
   Работает, даже если внешний мониторинг не настроен.
2. Внешний пинг (UptimeRobot) — рекомендуется как основной, дублирует первый.
3. Экономия ресурсов — таймер отслеживает простой и спит, когда трафика нет.

Почему так: Render усыпляет free-инстанс после ~15 минут простоя.
Один холодный старт занимает 30-90 секунд, поэтому дешевле держать
сервис разбуженным, чем поднимать заново на каждый запрос.
"""
import asyncio
import contextlib
import os
import time
from datetime import datetime, timezone

import httpx

from . import agent, config

KEEPALIVE_INTERVAL = 8 * 60  # 8 минут — с запасом до лимита 15 минут
_task: asyncio.Task | None = None


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [keepalive] {msg}", flush=True)


async def _ping_self() -> None:
    """Пингует собственный публичный URL, чтобы Render не усыпил инстанс."""
    url = config.SERVICE_URL
    if not url:
        return
    health = url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(health)
            _log(f"ping ok: {resp.status_code}")
    except Exception as exc:
        _log(f"ping failed: {type(exc).__name__}")


async def _loop() -> None:
    """Основной цикл: пинг + учёт простоя."""
    _log("anti-sleep запущен")
    while True:
        try:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            idle = agent.idle_seconds()

            if idle < config.IDLE_SLEEP_MINUTES * 60:
                await _ping_self()
            else:
                # Давно нет реальных пользователей — экономим, но сервис
                # всё равно должен отвечать на внешний пинг, поэтому ждём дальше
                _log(f"простой {int(idle // 60)} мин, пинг внешний")

            # Сбрасываем счётчик инструментов, чтобы не утекала память
            if agent.idle_seconds() > 3600:
                agent.touch()
                _log("сброс счётчика после часа простоя")
        except asyncio.CancelledError:
            _log("остановлен")
            raise
        except Exception as exc:
            _log(f"ошибка цикла: {type(exc).__name__}: {exc}")


def start() -> None:
    """Запускает фоновый таймер (при старте приложения)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None


def status() -> dict:
    return {
        "running": _task is not None and not _task.done(),
        "interval_minutes": KEEPALIVE_INTERVAL // 60,
        "idle_sleep_minutes": config.IDLE_SLEEP_MINUTES,
        "idle_seconds": round(agent.idle_seconds()),
        "service_url": config.SERVICE_URL or None,
        "external_monitor": bool(config.SERVICE_URL),
    }


def instructions() -> dict:
    """Подсказка для пользователя: как настроить внешний мониторинг."""
    return {
        "внешний_мониторинг": "UptimeRobot (бесплатно, 5 мониторов)",
        "тип": "HTTP GET",
        "интервал_минут": 9,
        "url": (config.SERVICE_URL or "https://<твой-сервис>.onrender.com").rstrip("/")
                + "/health",
        "зачем": "Render усыпляет free-инстанс после 15 мин простоя",
        "холодный_старт_секунд": "30-90",
    }