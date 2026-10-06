"""Проверка отмены задачи и параллельных запросов на живом сервере.

Самое важное для 512 МБ: два одновременных запроса не должны ронять сервис.

Запуск:  python tests/stress_check.py
"""
import json
import threading
import time
import urllib.error
import urllib.request

BASE = "https://light-agent.onrender.com"
lines: list[str] = []


def post(path: str, payload: dict, timeout: int = 240):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:120]}"


# ---------- 1. Отмена ----------
lines.append("=== Отмена задачи ===")
# Задача, которая точно долгая: агент будет делать много шагов
long_task = ("Создай папку cancel-test с 5 файлами, в каждом напиши "
             "10 строк текста, затем прочитай все и выведи размеры")

result: dict = {}


def runner():
    t0 = time.time()
    result["code"], result["body"] = post(
        "/api/chat", {"message": long_task, "session_id": "cancel-me"})
    result["t"] = time.time() - t0


th = threading.Thread(target=runner, daemon=True)
th.start()
time.sleep(2.5)  # даём задаче начаться

code, body = post("/api/cancel", {"session_id": "cancel-me"})
lines.append(f"POST /api/cancel -> {code}: {body[:120]}")

th.join(timeout=120)
if "code" in result:
    lines.append(f"задача завершилась: код {result['code']}, "
                 f"{result['t']:.1f}с")
    if result["code"] == 200:
        d = json.loads(result["body"])
        lines.append(f"  ok={d.get('ok')}, cancelled={d.get('cancelled')}, "
                     f"ошибка={str(d.get('error'))[:80]}")
        if d.get("cancelled") or "отмен" in str(d.get("error", "")).lower():
            lines.append("  ОТМЕНА СРАБОТАЛА")
        else:
            lines.append("  ВНИМАНИЕ: задача не была отменена "
                         "(возможно, успела завершиться)")
else:
    lines.append("  задача не завершилась за 120с - возможно, отмена сломала поток")

# ---------- 2. Параллельные запросы ----------
lines.append("")
lines.append("=== Три запроса одновременно ===")
results: list = []
lock = threading.Lock()


def one(i: int):
    c, b = post("/api/chat", {
        "message": f"Назови столицу страны номер {i}. Одно слово.",
        "session_id": f"par-{i}"})
    with lock:
        results.append((i, c, len(b)))


t0 = time.time()
ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
for t in ths:
    t.start()
for t in ths:
    t.join(timeout=180)
elapsed = time.time() - t0

lines.append(f"все три за {elapsed:.1f}с")
for i, c, ln in sorted(results):
    lines.append(f"  запрос {i}: код {c}, {ln} Б")
good = sum(1 for _, c, _ in results if c == 200)
lines.append(f"успешных: {good}/3")

# ---------- 3. Сервис жив после нагрузки ----------
lines.append("")
lines.append("=== Сервис после нагрузки ===")
try:
    req = urllib.request.Request(BASE + "/health",
                                 headers={"User-Agent": "probe"})
    with urllib.request.urlopen(req, timeout=180) as r:
        lines.append(f"/health -> {r.status}: {r.read().decode()[:120]}")
except Exception as e:
    lines.append(f"сервис не отвечает: {type(e).__name__}")

with open("reports/stress_check.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/stress_check.txt")
print("\n".join(lines))