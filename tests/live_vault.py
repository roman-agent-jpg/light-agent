"""Живая проверка хранилища секретов и инфраструктуры на Render.

Запуск:  python tests/live_vault.py
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

BASE = "https://light-agent.onrender.com"
TOKEN = dotenv_values(Path("credentials.txt")).get("ADMIN_TOKEN", "") \
    or dotenv_values(Path(".env")).get("ADMIN_TOKEN", "")

lines: list[str] = [f"Проверяю {BASE}", ""]
ok = bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        lines.append(f"OK   {label}" + (f"  — {detail}" if detail else ""))
    else:
        bad += 1
        lines.append(f"FAIL {label}" + (f"  — {detail}" if detail else ""))


def req(path: str, payload: dict | None = None, method: str = "GET",
        auth: bool = False, timeout: int = 180):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", "User-Agent": "probe"}
    if auth and TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    r = urllib.request.Request(BASE + path, data=data,
                               method=method if data else method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, x.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:150]}"


# 1. Статус: инфраструктура поднялась?
code, body = req("/api/status")
if code == 200:
    d = json.loads(body)
    inf = d.get("infra", {})
    lines.append("=== СТАТУС ===")
    lines.append(f"инструментов: {len(d.get('tools', []))}")
    lines.append(f"infra: {json.dumps(inf, ensure_ascii=False)}")
    check("хранилище секретов включено", inf.get("vault", {}).get("enabled"),
          json.dumps(inf.get("vault", {})))
    check("ключ Render доступен", inf.get("render"))
    check("Management API настроен", inf.get("management_api"))

    names = d.get("tools", [])
    for need in ("secret_set", "http_request", "infra_deploy", "infra_env"):
        check(f"инструмент {need} на сервере", need in names)
else:
    check("GET /api/status", False, str(code))

# 2. Хранилище доступно и читается
lines.append("")
lines.append("=== ХРАНИЛИЩЕ ===")
code, body = req("/api/vault")
check("GET /api/vault", code == 200, str(code))
if code == 200:
    d = json.loads(body)
    lines.append(f"секретов в базе: {len(d.get('secrets', []))}")
    lines.append(f"статус: {d.get('status')}")
    # Значений быть не должно - это главная гарантия
    check("значения не отдаются",
          all("value" not in s for s in d.get("secrets", [])))

# 3. Защита: без токена нельзя писать
code, body = req("/admin/vault", {"name": "RENDER_X", "value": "x"}, "POST")
check("запись без токена -> 401", code == 401, str(code))

# 4. Запись и чтение секрета с правильным токеном
NAME = "RENDER_LIVE_CHECK"
PROBE = "rnd_live_probe_8823"
if TOKEN:
    req(f"/admin/vault?name={NAME}", None, "DELETE", auth=True)

    code, body = req("/admin/vault", {"name": NAME, "value": PROBE,
                                      "note": "живая проверка"},
                     "POST", auth=True)
    check("запись секрета с токеном", code == 200, body[:120])

    if code == 200:
        code, body = req("/api/vault")
        got = [s for s in json.loads(body).get("secrets", [])
               if s["name"] == NAME]
        check("секрет появился в списке", bool(got))
        check("в списке нет значения", got and "value" not in got[0])

        # В базе должно лежать зашифрованное
        code, body = req(f"/admin/vault?name={NAME}", None, "DELETE", auth=True)
        check("удаление секрета", code == 200, body[:100])
    else:
        lines.append(f"  ответ сервера: {body[:300]}")
else:
    lines.append("ADMIN_TOKEN не найден - проверка записи пропущена")

# 5. Защита от неверного имени
code, body = req("/admin/vault", {"name": "mykey", "value": "x"},
                 "POST", auth=True)
check("имя без префикса отклонено", code == 400, str(code))

lines.append("")
lines.append(f"ИТОГО: успешно {ok}, проблем {bad}")

Path("reports/live_vault.txt").write_text("\n".join(lines), encoding="utf-8")
print("written reports/live_vault.txt")
print("\n".join(l.encode("ascii", "backslashreplace").decode("ascii")
                for l in lines))