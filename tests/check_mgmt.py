"""Проверка Supabase Management API: можно ли создать таблицу самому.

Python-клиент supabase не умеет DDL (нет RPC exec). Но Management API
позволяет выполнять SQL напрямую, если есть access token.
Это и даёт агенту автономность: он сам создаёт нужные таблицы.

Запуск:  python tests/check_mgmt.py
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

creds = dotenv_values(Path("credentials.txt"))
TOKEN = creds.get("SUPABASE_ACCESS_TOKEN", "")
REF = creds.get("SUPABASE_PROJECT_REF", "")
URL = creds.get("SUPABASE_URL", "")

# Извлекаем ref из URL, если отдельного поля нет
if not REF and URL:
    REF = URL.replace("https://", "").replace(".supabase.co", "").split("/")[0]

lines = [f"token: {'есть' if TOKEN else 'НЕТ'} (len={len(TOKEN)})",
         f"project ref: {REF}"]


def api(url: str, payload: dict | None = None, method: str = "GET") -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:200]}"


# 1. Кто мы
code, me = api("https://api.supabase.com/v1/projects")
lines.append("")
lines.append(f"GET /v1/projects -> {code}")
if code == 200 and isinstance(me, list):
    lines.append(f"  проектов: {len(me)}")
    for p in me[:8]:
        lines.append(f"    - {p.get('name')} ref={p.get('ref')} "
                     f"region={p.get('region')}")
elif code != 200:
    lines.append(f"  {str(me)[:300]}")

# 2. Выполнить SQL (read-only проба)
if REF and code == 200:
    code2, res = api(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        {"query": "select 1 as ok"}, "POST")
    lines.append("")
    lines.append(f"POST database/query -> {code2}")
    lines.append(f"  {str(res)[:300]}")
    if code2 == 200:
        lines.append("  ВЫВОД: Management API работает, таблицы можно создавать")

Path("reports/mgmt_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("written reports/mgmt_check.txt")
print("\n".join(lines))