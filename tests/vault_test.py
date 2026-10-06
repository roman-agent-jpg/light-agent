"""Проверка хранилища секретов: создание таблицы, шифрование, CRUD.

Запуск:  python tests/vault_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, vault

lines: list[str] = []
ok = bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        lines.append(f"OK   {label}" + (f"  — {detail}" if detail else ""))
    else:
        bad += 1
        lines.append(f"FAIL {label}" + (f"  — {detail}" if detail else ""))


# ---------- 1. Настройки ----------
lines.append("=== НАСТРОЙКИ ===")
lines.append(f"ADMIN_TOKEN: {'задан' if config.ADMIN_TOKEN and config.ADMIN_TOKEN != 'dev-token' else 'НЕ ЗАДАН'}")
lines.append(f"SUPABASE_URL: {'задан' if config.SUPABASE_URL else 'НЕТ'}")
lines.append(f"SUPABASE_ACCESS_TOKEN: {'задан' if config.SUPABASE_ACCESS_TOKEN else 'нет'}")
lines.append(f"SUPABASE_PROJECT_REF: {config.SUPABASE_PROJECT_REF or 'НЕТ'}")

# ---------- 2. Шифрование ----------
lines.append("")
lines.append("=== ШИФРОВАНИЕ ===")
try:
    secret = "rnd_TESTKEY-do-not-log-12345"
    enc = vault.encrypt(secret)
    dec = vault.decrypt(enc)
    check("шифрование и расшифровка совпадают", dec == secret)
    check("шифротекст не содержит исходного",
          secret not in enc, enc[:40])
    check("шифротекст выглядит как Fernet",
          enc.startswith("gAAAA"), enc[:20] + "...")
    check("одинаковые значения дают разный шифротекст",
          vault.encrypt("одинаково") != vault.encrypt("одинаково"),
          "Fernet использует случайный IV")
except Exception as exc:
    check("шифрование работает", False, f"{type(exc).__name__}: {exc}")

# ---------- 3. Валидация имён ----------
lines.append("")
lines.append("=== ПРОВЕРКА ИМЁН ===")
for bad_name, why in [
    ("mykey", "без допустимого префикса"),
    ("ADMIN_TOKEN", "защищённое имя"),
    ("", "пустое имя"),
    ("RENDER_API KEY", "пробел в имени"),
]:
    valid, msg = vault._valid_name(bad_name)
    check(f"отклонено «{bad_name or '(пусто)'}»", not valid, msg)
valid, _ = vault._valid_name("RENDER_API_KEY")
check("принято RENDER_API_KEY", valid)
valid, _ = vault._valid_name("GITHUB_TOKEN")
check("принято GITHUB_TOKEN", valid)

# ---------- 4. Таблица ----------
lines.append("")
lines.append("=== ТАБЛИЦА ===")
res = vault.ensure_table()
lines.append(f"ensure_table: {res}")
check("таблица доступна", res.get("ok"), str(res.get("reason", "")))

# ---------- 5. CRUD ----------
lines.append("")
lines.append("=== CRUD ===")
NAME = "RENDER_TEST_KEY"
vault.delete_secret(NAME)

r = vault.set_secret(NAME, "rnd_fake_value_for_test", "тестовый ключ")
check("запись секрета", r.get("ok"), r.get("error", ""))

if r.get("ok"):
    got = vault.get_secret(NAME)
    check("чтение секрета", got.get("ok") and
          got.get("value") == "rnd_fake_value_for_test",
          got.get("error", "")[:100])

    # Проверяем, что в базе лежит шифротекст, а не открытый текст
    client = vault._get_client()
    if client is not None:
        raw = (client.table(vault.SECRETS_TABLE)
               .select("value").eq("name", NAME).limit(1).execute())
        stored = (raw.data or [{}])[0].get("value", "")
        check("в базе лежит шифротекст",
              "rnd_fake_value_for_test" not in stored and stored.startswith("gAAAA"),
              stored[:24] + "...")

    items = vault.list_secrets()
    check("список секретов", any(s["name"] == NAME for s in items),
          f"всего {len(items)}")
    check("в списке нет значений",
          all("value" not in s for s in items), "значения не отдаются")

    # Повторная запись = обновление
    r2 = vault.set_secret(NAME, "rnd_second_value")
    check("повторная запись обновляет", r2.get("action") == "updated",
          str(r2.get("action")))

    # Перезапись защищённого имени
    r3 = vault.set_secret("ADMIN_TOKEN", "взлом")
    check("защищённое имя не перезаписать", not r3.get("ok"),
          r3.get("error", "")[:80])

    d = vault.delete_secret(NAME)
    check("удаление секрета", d.get("ok"), d.get("error", ""))
    gone = vault.get_secret(NAME)
    check("после удаления секрета нет", not gone.get("ok"))

# ---------- 6. Инфраструктура ----------
lines.append("")
lines.append("=== ИНФРАСТРУКТУРА ===")
from app import infra

st = infra.status()
lines.append(f"infra.status: {st}")
check("хранилище доступно", st["vault"]["enabled"])
check("ключ Render на месте (настройки или хранилище)", st["render"])
check("Management API настроен", st["management_api"])

# ---------- 7. Защита http_request ----------
lines.append("")
lines.append("=== ЗАЩИТА HTTP ===")
for bad_url, why in [
    ("http://api.render.com/v1/services", "не https"),
    ("https://localhost/admin", "локальный хост"),
    ("https://169.254.169.254/latest/meta-data/", "метаданные облака"),
]:
    out = infra.tool_http_request("GET", bad_url)
    check(f"заблокирован {why}", "Только https" in out or "заблокирован" in out,
          out[:70])

out = infra.tool_http_request("GET", "ftp://example.com")
check("заблокирован неверный протокол", "Только https" in out, out[:70])

# ---------- Итог ----------
lines.append("")
lines.append("=" * 54)
lines.append(f"ПРОЙДЕНО: {ok}   ПРОВАЛЕНО: {bad}")
if bad:
    lines.append("")
    lines.append("Провалено:")
    for l in lines:
        if l.startswith("FAIL"):
            lines.append("  " + l)
lines.append("=" * 54)

Path("reports/vault_test.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(l.encode("ascii", "backslashreplace").decode("ascii") for l in lines))