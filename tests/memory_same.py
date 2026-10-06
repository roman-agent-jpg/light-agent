"""Проверка памяти в рамках ОДНОЙ сессии.

Предыдущий тест использовал разные session_id, поэтому история не
подтягивалась - это была ошибка теста, а не кода.

Здесь: одна сессия, два сообщения подряд. Агент должен помнить первое,
когда отвечает на второе.

Запуск:  python memory_same.py
"""
from app import agent, memory

S = "same-session-test"

# Чистим прошлый запуск, чтобы результат был честным
memory.clear_session(S)

lines = [f"сессия {S!r} очищена"]

lines.append("")
lines.append("Шаг 1: первый вопрос")
r1 = agent.run("Мой любимый цвет - синий. Запомни это.", S)
lines.append(f"  ok={r1.get('ok')} answer={r1.get('answer')!r}")

lines.append("")
lines.append("Шаг 2: второй вопрос (та же сессия)")
r2 = agent.run("Какой мой любимый цвет? Ответь одним словом.", S)
lines.append(f"  ok={r2.get('ok')} answer={r2.get('answer')!r}")

lines.append("")
hist = memory.get_history(S, limit=20)
lines.append(f"в базе для этой сессии: {len(hist)} сообщений")
for h in hist:
    lines.append(f"  {h['role']}: {h['content'][:80]!r}")

lines.append("")
ans = str(r2.get("answer", ""))
if "синий" in ans.lower():
    lines.append("RESULT: ПАМЯТЬ РАБОТАЕТ")
else:
    lines.append("RESULT: агент не вспомнил первый вопрос")

with open("reports/memory_same.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/memory_same.txt")