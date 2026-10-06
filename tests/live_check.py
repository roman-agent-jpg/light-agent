"""Живая проверка сервиса на Render: все маршруты, включая новые.

Запуск:  python tests/live_check.py
"""
import json
import time
import urllib.error
import urllib.request

BASE = "https://light-agent.onrender.com"

lines: list[str] = [f"Проверяю {BASE}", ""]


def probe(path: str, timeout: int = 180) -> tuple[int, str, dict]:
    try:
        req = urllib.request.Request(BASE + path,
                                     headers={"User-Agent": "probe"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:150]}", {}


# Render free засыпает — первый запрос может занять до 90 секунд
for attempt in range(4):
    code, body, _ = probe("/health")
    lines.append(f"попытка {attempt + 1}: /health -> {code}")
    if code == 200:
        lines.append(f"  {body[:200]}")
        break
    time.sleep(6)

# Маршруты: (путь, ожидаемый код, что проверяем)
ROUTES = [
    ("/", 200, "интерфейс"),
    ("/static/app.css", 200, "стили"),
    ("/static/app.js", 200, "скрипт"),
    ("/api/status", 200, "статус"),
    ("/api/tree", 200, "дерево файлов"),
    ("/api/sessions", 200, "сессии"),
    ("/api/file?path=nope.txt", 404, "нет файла -> 404"),
    ("/api/raw?path=nope.txt", 404, "нет картинки -> 404"),
    ("/api/file?path=../../etc/passwd", 400, "выход за папку -> 400"),
]

lines.append("")
lines.append("=== Маршруты ===")
ok = bad = 0
for path, expect, label in ROUTES:
    code, body, hdrs = probe(path, timeout=120)
    good = code == expect
    ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
    lines.append(f"{'OK  ' if good else 'FAIL'} {label:22} {code} (ждали {expect})")
    if good and path == "/api/status":
        try:
            d = json.loads(body)
            lines.append(f"       инструментов: {len(d.get('tools', []))}")
            lines.append(f"       память: {d['memory']['enabled']}, "
                         f"хранилище: {d['storage']['enabled']}")
            lines.append(f"       anti-sleep: {d['anti_sleep']['running']}")
        except Exception as e:
            lines.append(f"       не разобрал JSON: {e}")
    lines.append("")
lines.append("=== Статика: реальные заголовки ===")
for path, want in (("/static/app.css", "text/css"),
                   ("/static/app.js", "javascript")):
    code, body, hdrs = probe(path, timeout=120)
    ct = hdrs.get("Content-Type", "?")
    lines.append(f"{path} -> {code}, {len(body)} Б, content-type: {ct}")
    if want not in ct.lower():
        lines.append(f"  ВНИМАНИЕ: ожидали {want}")
        bad += 1
    else:
        ok += 1

# SSE: проверяем, что события идут
lines.append("")
lines.append("=== SSE-стриминг ===")
try:
    req = urllib.request.Request(
        BASE + "/api/chat/stream",
        data=json.dumps({"message": "Назови столицу Франции одним словом",
                         "session_id": "live-sse"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        ct = r.headers.get("content-type", "")
        raw = r.read().decode("utf-8", "replace")
    lines.append(f"content-type: {ct}")
    lines.append(f"есть open: {'event: open' in raw}")
    lines.append(f"есть done: {'event: done' in raw}")
    lines.append(f"есть tool/step: {'event: step' in raw}")
    lines.append(f"тело (200 симв.): {raw[:200]}")
    if "event: done" not in raw:
        bad += 1
    else:
        ok += 1
    if "text/event-stream" not in ct:
        lines.append("ВНИМАНИЕ: не SSE")
        bad += 1
except Exception as e:
    lines.append(f"SSE ошибка: {type(e).__name__} {str(e)[:200]}")
    bad += 1

lines.append("")
lines.append("=== PWA на проде ===")
PWA = [
    ("/manifest.json", 200, "манифест"),
    ("/static/sw.js", 200, "service worker"),
    ("/static/icons/icon-192.png", 200, "иконка 192"),
    ("/static/icons/icon-512.png", 200, "иконка 512"),
    ("/static/icons/maskable-512.png", 200, "maskable-иконка"),
    ("/static/icons/icon-180.png", 200, "apple-touch-icon"),
]
for path, expect, label in PWA:
    code, body, hdrs = probe(path, timeout=120)
    good = code == expect
    ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
    ctype = next((v for k, v in hdrs.items() if k.lower() == "content-type"), "?")
    lines.append(f"{'OK  ' if good else 'FAIL'} {label:22} {code} "
                 f"{len(body)} Б {ctype}")

# Заголовки на главной нужны для установки.
# Сервер отдаёт имена в нижнем регистре (HTTP/2), а urllib сохраняет
# регистр как есть — ищем без учёта регистра, иначе тест врёт.
code, body, hdrs = probe("/", timeout=120)
lower = {k.lower(): v for k, v in hdrs.items()}
swa = lower.get("service-worker-allowed", "")
cc = lower.get("cache-control", "")
lines.append(f"Service-Worker-Allowed: {swa or 'НЕТ'}")
lines.append(f"Cache-Control: {cc or 'НЕТ'}")
if swa != "/":
    lines.append("  ВНИМАНИЕ: нет Service-Worker-Allowed")
    bad += 1
else:
    ok += 1

for needle, label in (('rel="manifest"', "HTML: link на манифест"),
                      ("viewport-fit=cover", "HTML: безопасные зоны"),
                      ('id="bInstall"', "HTML: кнопка установки")):
    if needle in body:
        lines.append(f"OK   {label}")
        ok += 1
    else:
        lines.append(f"FAIL {label}")
        bad += 1

lines.append("")
lines.append(f"ИТОГО: успешно {ok}, проблем {bad}")

with open("reports/live_check.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/live_check.txt")
print("\n".join(lines))