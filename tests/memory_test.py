"""Проверка памяти: помнит ли агент прошлый разговор.

Сценарий:
  сессия A - говорим "мой город Киев, я программист"
  сессия B (новая!) - спрашиваем "какой мой город и чем я занимаюсь"
Если Б - пустая сессия, агент должен достать это из памяти.

Запуск:  python memory_test.py
"""
from app import agent, memory

lines = []
lines.append(f"memory.status(): {memory.status()}")
lines.append("")

# Сессия 1: сообщаем факты
lines.append("SESSION A: сообщаю факты")
r1 = agent.run("Запомни: мой город Киев, я занимаюсь программированием на Python. "
               "Ответь одним словом: принято.", "memA")
lines.append(f"  ok={r1.get('ok')} answer={r1.get('answer')!r}")

# Проверяем, что запись легла в базу
hist = memory.get_history("memA", limit=10)
lines.append(f"  в базе сообщений: {len(hist)}")
for h in hist:
    lines.append(f"    {h.get('role')}: {str(h.get('content'))[:70]!r}")

lines.append("")
lines.append("SESSION B (новая сессия, пустая): спрашиваю про факты")
r2 = agent.run("Какой мой город и чем я занимаюсь? Ответь кратко.", "memB")
lines.append(f"  ok={r2.get('ok')} answer={r2.get('answer')!r}")
lines.append(f"  steps={len(r2.get('steps', []))}")

lines.append("")
if r2.get("ok") and "Киев" in str(r2.get("answer", "")):
    lines.append("RESULT: ПАМЯТЬ РАБОТАЕТ - агент вспомнил город из прошлой сессии")
else:
    lines.append("RESULT: память не сработала или ответ неполный")

with open("reports/memory_test.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/memory_test.txt")