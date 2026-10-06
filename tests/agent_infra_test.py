"""Проверка: агент САМ пользуется инфраструктурными инструментами.

Не вызов функций напрямую, а задача агенту на естественном языке.
Смотрим, какие инструменты он вызвал и что ответил.

Запуск:  python tests/agent_infra_test.py
"""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent, vault

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


def run_capture(task: str, session: str) -> dict:
    """Запускает агента и собирает список вызванных инструментов."""
    calls: list[tuple[str, dict]] = []

    def on_event(kind: str, payload: dict) -> None:
        if kind == "tool":
            calls.append((payload.get("name", ""), payload.get("args", {})))

    res = agent.run(task, session, on_event=on_event)
    res["_calls"] = calls
    return res


# ---------- 1. Агент читает инфраструктуру ----------
lines.append("=== АГЕНТ ПРОВЕРЯЕТ ИНФРАСТРУКТУРУ ===")
r = run_capture(
    "Покажи все сервисы на Render: имена, id и адреса. "
    "Не делай ничего другого, только посмотри.",
    "infra-check-1")

names = [n for n, _ in r["_calls"]]
lines.append(f"инструменты: {names}")
lines.append(f"ответ: {str(r.get('answer'))[:400]}")
lines.append("")

check("агент вызвал инструмент инфраструктуры",
      any(n.startswith("infra_") for n in names), str(names))
check("агент получил ответ", bool(r.get("ok")), str(r.get("error"))[:120])
check("в ответе есть наш сервис",
      "light-agent" in str(r.get("answer", "")),
      str(r.get("answer"))[:150])
check("агент не полез в файлы проекта",
      not any(n in ("write_file", "edit_file", "delete_file") for n in names),
      str(names))

# ---------- 2. Агент сохраняет секрет ----------
lines.append("")
lines.append("=== АГЕНТ СОХРАНЯЕТ СЕКРЕТ ===")

# Убираем все возможные варианты имени, какие агент может выбрать
for n in ("AGENT_TEST_KEY", "AG_TEST_KEY"):
    vault.delete_secret(n)

r2 = run_capture(
    "Сохрани в защищённое хранилище секрет с именем AGENT_TEST_KEY "
    "и значением «agent-secret-probe-123». Больше ничего не делай.",
    "infra-check-2")

names2 = [n for n, _ in r2["_calls"]]
set_args = [a for n, a in r2["_calls"] if n == "secret_set"]
lines.append(f"инструменты: {names2}")
lines.append(f"аргументы: {json.dumps(set_args, ensure_ascii=False)[:250]}")
lines.append(f"ответ: {str(r2.get('answer'))[:250]}")
lines.append("")

check("агент вызвал secret_set", "secret_set" in names2, str(names2))

# Какое имя агент выбрал на самом деле - смотрим по базе
stored_names = [s["name"] for s in vault.list_secrets()
                if "TEST_KEY" in s["name"]]
lines.append(f"в хранилище после задачи: {stored_names}")
check("секрет сохранился в хранилище", bool(stored_names), str(stored_names))

ACTUAL = stored_names[0] if stored_names else "AGENT_TEST_KEY"

# Ключ должен лежать в базе зашифрованным
got = vault.get_secret(ACTUAL)
check("значение читается обратно",
      got.get("value") == "agent-secret-probe-123",
      f"{ACTUAL}: {str(got.get('error'))[:80]}")

client = vault._get_client()
if client is not None and stored_names:
    raw = (client.table(vault.SECRETS_TABLE)
           .select("value").eq("name", ACTUAL).limit(1).execute())
    encrypted = (raw.data or [{}])[0].get("value", "")
    check("в базе шифротекст, а не открытый ключ",
          "agent-secret-probe-123" not in encrypted
          and encrypted.startswith("gAAAA"),
          encrypted[:24] + "...")

check("агент не записал ключ в файл проекта",
      not any(n in ("write_file", "edit_file") for n in names2), str(names2))

# ---------- 3. Агент перечисляет секреты ----------
lines.append("")
lines.append("=== АГЕНТ ЧИТАЕТ СПИСОК СЕКРЕТОВ ===")
r3 = run_capture("Покажи список секретов в защищённом хранилище.",
                  "infra-check-3")
names3 = [n for n, _ in r3["_calls"]]
lines.append(f"инструменты: {names3}")
lines.append(f"ответ: {str(r3.get('answer'))[:300]}")
check("агент вызвал secret_list", "secret_list" in names3, str(names3))
check("агент не показал значение секрета",
      "agent-secret-probe-123" not in str(r3.get("answer", "")),
      "значение не должно попадать в ответ")

# ---------- 4. Агент управляет переменными ----------
lines.append("")
lines.append("=== АГЕНТ УПРАВЛЯЕТ ПЕРЕМЕННЫМИ ===")
r4 = run_capture(
    "Покажи переменные окружения сервиса light-agent на Render.",
    "infra-check-4")
names4 = [n for n, _ in r4["_calls"]]
args4 = [a for n, a in r4["_calls"] if n == "infra_env"]
lines.append(f"инструменты: {names4}")
lines.append(f"аргументы infra_env: {json.dumps(args4, ensure_ascii=False)[:250]}")
lines.append(f"ответ: {str(r4.get('answer'))[:250]}")
check("агент вызвал infra_env", "infra_env" in names4, str(names4))
check("агент передал service_id",
      args4 and args4[0].get("service_id"), str(args4)[:120])

# ---------- Уборка ----------
for n in ("AGENT_TEST_KEY", "AG_TEST_KEY"):
    vault.delete_secret(n)
for s in ("infra-check-1", "infra-check-2", "infra-check-3", "infra-check-4"):
    from app import memory
    memory.clear_session(s)

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

Path("reports/agent_infra.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(l.encode("ascii", "backslashreplace").decode("ascii")
                for l in lines))