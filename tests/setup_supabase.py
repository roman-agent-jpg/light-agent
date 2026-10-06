"""Создаёт таблицы в Supabase через Management API и переносит токен в .env.

Python-клиент supabase не умеет DDL. Management API умеет, но требует
SUPABASE_ACCESS_TOKEN, которого нет в .env - он лежит в credentials.txt.
Этот скрипт переносит нужные значения в .env и создаёт таблицы.

Запуск:  python tests/setup_supabase.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values, set_key

from app import config

creds = dotenv_values(Path("credentials.txt"))
env_path = Path(".env")

lines: list[str] = []

# ---------- 1. Переносим токен и ref в .env ----------
MOVE = {
    "SUPABASE_ACCESS_TOKEN": creds.get("SUPABASE_ACCESS_TOKEN", ""),
    "RENDER_API_KEY": creds.get("RENDER_API_KEY", ""),
    "RENDER_OWNER_ID": "tea-db1as9ugekts73d041dg",
}

for key, value in MOVE.items():
    if not value:
        lines.append(f"{key}: нет в credentials.txt, пропускаю")
        continue
    current = (dotenv_values(env_path).get(key) or "").strip()
    if current == value:
        lines.append(f"{key}: уже в .env")
        continue
    set_key(str(env_path), key, value, quote_mode="never")
    lines.append(f"{key}: записан в .env (len={len(value)})")

# ---------- 2. Перезагружаем конфиг ----------
import importlib
importlib.reload(config)

lines.append("")
lines.append(f"SUPABASE_ACCESS_TOKEN: {'есть' if config.SUPABASE_ACCESS_TOKEN else 'НЕТ'}")
lines.append(f"SUPABASE_PROJECT_REF:  {config.SUPABASE_PROJECT_REF or 'НЕТ'}")
lines.append(f"RENDER_API_KEY:         {'есть' if config.RENDER_API_KEY else 'НЕТ'}")
lines.append(f"RENDER_OWNER_ID:        {config.RENDER_OWNER_ID or 'НЕТ'}")

# ---------- 3. Создаём таблицы ----------
from app import vault

if not (config.SUPABASE_ACCESS_TOKEN and config.SUPABASE_PROJECT_REF):
    lines.append("")
    lines.append("Management API не настроен - таблицу создать нечем.")
else:
    import httpx

    DDL = vault.DDL
    url = (f"https://api.supabase.com/v1/projects/"
           f"{config.SUPABASE_PROJECT_REF}/database/query")
    try:
        r = httpx.post(url,
                       headers={"Authorization":
                                f"Bearer {config.SUPABASE_ACCESS_TOKEN}",
                                "Content-Type": "application/json"},
                       json={"query": DDL}, timeout=60.0)
        lines.append("")
        lines.append(f"DDL -> {r.status_code}")
        lines.append(f"ответ: {r.text[:400]}")
    except Exception as exc:
        lines.append("")
        lines.append(f"DDL ошибка: {type(exc).__name__}: {exc}")

# ---------- 4. Проверяем ----------
res = vault.ensure_table()
lines.append("")
lines.append(f"ensure_table: {res}")

Path("reports/setup_supabase.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))