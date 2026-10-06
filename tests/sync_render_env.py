"""Задаёт переменные окружения на Render через API.

Новые настройки (SUPABASE_ACCESS_TOKEN, RENDER_API_KEY, RENDER_OWNER_ID)
есть в .env локально, но не в окружении сервиса на Render - поэтому
инфраструктурные инструменты там отвечают «нет ключа».

Скрипт читает .env и заливает недостающие переменные через Render API.

Запуск:  python tests/sync_render_env.py
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

creds = dotenv_values(Path("credentials.txt"))
env = dotenv_values(Path(".env"))
KEY = env.get("RENDER_API_KEY") or creds.get("RENDER_API_KEY", "")
OWNER = env.get("RENDER_OWNER_ID") or "tea-db1as9ugekts73d041dg"
SERVICE = "srv-db1coonavr4c73b9vbu0"   # id нашего сервиса

# Переменные, которых не хватает сервису
WANT = {
    "SUPABASE_ACCESS_TOKEN": creds.get("SUPABASE_ACCESS_TOKEN", ""),
    "RENDER_API_KEY": creds.get("RENDER_API_KEY", ""),
    "RENDER_OWNER_ID": OWNER,
}

lines = [f"RENDER_API_KEY: {'есть' if KEY else 'НЕТ'}",
         f"service: {SERVICE}", ""]


def api(method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        f"https://api.render.com/v1{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json",
                 **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            b = r.read().decode("utf-8")
            return r.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:200]}"


# 1. Что уже есть на сервере
#    Render оборачивает каждую переменную в {"envVar": {...}},
#    поэтому ключ лежит во вложенном объекте.
code, existing = api("GET", f"/services/{SERVICE}/env-vars")
have = set()
if code == 200 and isinstance(existing, list):
    for e in existing:
        v = e.get("envVar", e) if isinstance(e, dict) else {}
        if v.get("key"):
            have.add(v["key"])
    lines.append(f"на сервере уже {len(have)} переменных")
else:
    lines.append(f"не удалось прочитать текущие: {code} {str(existing)[:150]}")

# 1b. Убираем мусор от проверок
for junk in ("AG_PROBE_123", "AG_VERIFY_TMP"):
    if junk in have:
        code, _ = api("DELETE", f"/services/{SERVICE}/env-vars/{junk}")
        lines.append(f"удалён мусор {junk} (код {code})")
        have.discard(junk)

# 2. Доливаем недостающие.
#    Render принимает ТОЛЬКО PUT на путь с именем переменной:
#      PUT /v1/services/<id>/env-vars/<KEY>  {"value": "..."}  -> 200
#    PUT/POST на коллекцию дают 400 «invalid JSON» или 405.
added = skipped = 0
for k, v in WANT.items():
    if not v:
        lines.append(f"{k}: нет значения, пропускаю")
        continue
    if k in have:
        lines.append(f"{k}: уже есть на сервере")
        continue
    code, res = api("PUT", f"/services/{SERVICE}/env-vars/{k}", {"value": v})
    if code < 300:
        added += 1
        lines.append(f"{k}: добавлена (код {code})")
    else:
        skipped += 1
        lines.append(f"{k}: ошибка {code} {str(res)[:200]}")

lines.append("")
lines.append(f"добавлено: {added}, пропущено: {skipped}")

# 3. Проверка
code, after = api("GET", f"/services/{SERVICE}/env-vars")
if code == 200 and isinstance(after, list):
    lines.append(f"теперь на сервере {len(after)} переменных")

Path("reports/sync_render_env.txt").write_text("\n".join(lines), encoding="utf-8")
print("written reports/sync_render_env.txt")
print("\n".join(lines))